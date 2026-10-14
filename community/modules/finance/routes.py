"""Kasse pages: the member's own Kontoauszug, and the Kassenwart's workspace.

Only the Kassenwart and the Kassenprüfer see the whole Kasse (finance.ledger.view_all, Q22);
every member sees their own Saldo (finance.saldo.view_own).
"""

import secrets
from datetime import date

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_, select

from community.core.authz import can, require_perm
from community.core.identity.models import Member, MemberStatus
from community.core.money import format_cents
from community.core.org import services as org
from community.core.org.models import MemberGroup
from community.core.time import today_local
from community.core.widgets import profile_card
from community.extensions import db

from . import bp, charges, cosign
from . import services as ledger
from .forms import (
    AccountEditForm,
    AccountForm,
    BookingForm,
    ButtonForm,
    CategoryEditForm,
    CategoryForm,
    RecurringForm,
    RejectForm,
    ReverseForm,
    UmlageForm,
)
from .models import AccountKind, FinAccount, FinCategory, FinJournal, FinRecurring, FinRequest, RequestStatus

JOURNAL_PAGE_LIMIT = 300


@bp.app_template_filter('account_label')
def account_label_filter(account):
    return ledger.account_label(account)


# --- The member's own Saldo ------------------------------------------------------------


@profile_card('finance/_saldo_card.html', permission='finance.saldo.view_own', order=10)
def saldo_card(member):
    account = ledger.member_account(member, create=False)
    rows = ledger.statement(account)[1][-3:][::-1] if account is not None else []
    return {'saldo': ledger.saldo(member), 'recent': rows, 'payment_ref': member.payment_ref}


@bp.route('/mein-konto')
@require_perm('finance.saldo.view_own')
def mine():
    member = current_user.member
    account = ledger.member_account(member, create=False)
    opening, rows, closing = ledger.statement(account) if account is not None else (0, [], 0)
    return render_template(
        'finance/mine.html', rows=rows[::-1], saldo=closing, payment_ref=member.payment_ref, member=member
    )


# --- Kassenwart: overview and Kontokarten --------------------------------------------------


def _member_or_404(public_id):
    member = db.session.scalar(select(Member).where(Member.public_id == public_id))
    if member is None:
        abort(404)
    return member


@bp.route('/')
@require_perm('finance.ledger.view_all')
def overview():
    only_debts = request.args.get('nur') == 'schulden'
    saldi = ledger.member_saldi()
    owed = sum(cents for _, cents in saldi if cents < 0)
    credit = sum(cents for _, cents in saldi if cents > 0)
    rows = [row for row in saldi if row[1] < 0] if only_debts else saldi
    balances = ledger.account_balances()
    money = [(account, debit - credit_) for account, debit, credit_ in balances if account.kind in AccountKind.MONEY]
    return render_template(
        'finance/overview.html',
        rows=rows,
        owed=owed,
        credit=credit,
        money=money,
        only_debts=only_debts,
        recent=ledger.journals(limit=8),
        open_requests=len(cosign.open_requests()),
    )


@bp.route('/kontokarte/<public_id>')
@require_perm('finance.ledger.view_all')
def card(public_id):
    member = _member_or_404(public_id)
    account = ledger.member_account(member, create=False)
    opening, rows, closing = ledger.statement(account) if account is not None else (0, [], 0)
    return render_template('finance/card.html', member=member, rows=rows[::-1], saldo=closing)


# --- Kassenbuch (journal) ----------------------------------------------------------------


def _int_arg(name):
    value = request.args.get(name, type=int)
    return value if value and value > 0 else None


@bp.route('/kassenbuch')
@require_perm('finance.ledger.view_all')
def journal():
    account_id, category_id = _int_arg('konto'), _int_arg('kostenstelle')
    account = db.session.get(FinAccount, account_id) if account_id else None
    category = db.session.get(FinCategory, category_id) if category_id else None
    items = ledger.journals(account=account, category=category, limit=JOURNAL_PAGE_LIMIT + 1)
    return render_template(
        'finance/journal.html',
        items=items[:JOURNAL_PAGE_LIMIT],
        truncated=len(items) > JOURNAL_PAGE_LIMIT,
        account=account,
        category=category,
        accounts=ledger.accounts(include_archived=True),
        categories=ledger.categories(include_archived=True),
    )


@bp.route('/buchung/<int:journal_id>')
@require_perm('finance.ledger.view_all')
def booking(journal_id):
    item = db.get_or_404(FinJournal, journal_id)
    return render_template('finance/booking.html', item=item, form=ReverseForm(), pending=_pending_reversal(item))


def _pending_reversal(journal):
    query = select(FinRequest).where(
        FinRequest.reverses_journal_id == journal.id, FinRequest.status == RequestStatus.OPEN
    )
    return db.session.scalar(query)


@bp.route('/buchung/<int:journal_id>/storno', methods=['POST'])
@require_perm('finance.journal.reverse')
def reverse(journal_id):
    item = db.get_or_404(FinJournal, journal_id)
    form = ReverseForm()
    if not form.validate_on_submit():
        flash('Bitte einen Grund für das Storno angeben.', 'danger')
        return redirect(url_for('finance.booking', journal_id=item.id))
    try:
        if request.form.get('vorlegen'):
            pending, _ = cosign.request_reversal(item, requested_by=current_user, reason=form.reason.data)
        else:
            reversal = ledger.reverse(item, reversed_by=current_user, reason=form.reason.data)
    except ledger.CosignRequired:
        db.session.rollback()
        return render_template('finance/booking.html', item=item, form=form, cosign_needed=True, pending=None)
    except ledger.FinanceError as error:
        db.session.rollback()
        flash(str(error), 'danger')
        return redirect(url_for('finance.booking', journal_id=item.id))
    db.session.commit()
    if request.form.get('vorlegen'):
        flash(SUBMITTED_FOR_COSIGN, 'success')
        return redirect(url_for('finance.cosign_request', request_id=pending.id))
    flash('Storniert.', 'success')
    return redirect(url_for('finance.booking', journal_id=reversal.id))


SUBMITTED_FOR_COSIGN = 'Zur Gegenzeichnung vorgelegt. Gebucht wird erst, wenn der Kassenprüfer bestätigt.'


# --- Booking form --------------------------------------------------------------------------

# Presets for the usual cases: (Soll, Haben, Beleg series). 'member' is the chosen member's card.
PRESETS = {
    'zahlung': ('giro', 'member', 'G', 'Zahlung'),
    'bar': ('bar', 'member', 'K', 'Bareinzahlung'),
    'belastung': ('member', 'verschiedenes', 'B', ''),
    'auslage': ('verschiedenes', 'member', 'B', 'Auslage'),
}
BELEG_SERIES = ('G', 'K', 'B')


def _members_for_choices():
    """Active members and everyone who already has a Kontokarte (e.g. a former member who owes)."""
    with_card = select(FinAccount.member_id).where(FinAccount.member_id.is_not(None))
    query = select(Member).where(or_(Member.status == MemberStatus.ACTIVE, Member.id.in_(with_card)))
    return db.session.scalars(query.order_by(Member.last_name, Member.first_name)).all()


def _account_choices():
    groups = {}
    for account in ledger.accounts():
        groups.setdefault(account.kind_label, []).append((f'a{account.id}', ledger.account_label(account)))
    groups['Kontokarten'] = [(f'm{m.id}', m.full_name) for m in _members_for_choices()]
    return groups


def _resolve(value):
    """'a12' -> account 12; 'm7' -> member 7's Kontokarte (created on first use)."""
    if not value or value[0] not in 'am' or not value[1:].isdigit():
        return None
    if value[0] == 'a':
        return db.session.get(FinAccount, int(value[1:]))
    member = db.session.get(Member, int(value[1:]))
    return ledger.member_account(member) if member is not None else None


def _preset_value(side, member):
    if side == 'member':
        return f'm{member.id}' if member is not None else ''
    account = ledger.account_by_key(side)
    return f'a{account.id}' if account is not None else ''


@bp.route('/buchung/neu', methods=['GET', 'POST'])
@require_perm('finance.journal.post')
def new_booking():
    form = BookingForm()
    form.debit.choices = form.credit.choices = {'Auswahl': [('', '– bitte wählen –')], **_account_choices()}
    form.category.choices = [('', '– keine –')] + [(str(c.id), c.name) for c in ledger.categories()]
    member = None
    if request.method == 'GET':
        form.booked_on.data = today_local()
        if request.args.get('mitglied'):
            member = _member_or_404(request.args['mitglied'])
        preset = PRESETS.get(request.args.get('vorlage', ''))
        if preset:
            debit, credit, series, title = preset
            form.debit.data = _preset_value(debit, member)
            form.credit.data = _preset_value(credit, member)
            form.beleg.data = ledger.next_beleg(series, form.booked_on.data)
            form.title.data = title
    cosign_needed = False
    if form.validate_on_submit():
        debit, credit = _resolve(form.debit.data), _resolve(form.credit.data)
        category = db.session.get(FinCategory, int(form.category.data)) if form.category.data else None
        booking_args = (
            ledger.kind_for(debit, credit),
            form.title.data,
            form.booked_on.data,
            [ledger.Line(debit, credit, form.amount.cents, category)],
        )
        submit_for_cosign = bool(request.form.get('vorlegen'))
        try:
            if submit_for_cosign:
                pending, _ = cosign.request_booking(
                    *booking_args, requested_by=current_user, beleg=form.beleg.data, note=form.note.data
                )
            else:
                journal, _ = ledger.post_journal(
                    *booking_args, posted_by=current_user, beleg=form.beleg.data, note=form.note.data
                )
        except ledger.CosignRequired:
            db.session.rollback()
            cosign_needed = True
        except ledger.FinanceError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            if submit_for_cosign:
                flash(SUBMITTED_FOR_COSIGN, 'success')
                return redirect(url_for('finance.cosign_request', request_id=pending.id))
            flash(f'Gebucht: {journal.title}', 'success')
            if request.form.get('weiter'):
                return redirect(url_for('finance.new_booking'))
            return redirect(url_for('finance.booking', journal_id=journal.id))
    day = form.booked_on.data or today_local()
    next_numbers = [ledger.next_beleg(series, day) for series in BELEG_SERIES]
    return render_template(
        'finance/new_booking.html', form=form, next_numbers=next_numbers, member=member, cosign_needed=cosign_needed
    )


# --- Accounts and cost centres ----------------------------------------------------------


@bp.route('/konten', methods=['GET', 'POST'])
@require_perm('finance.ledger.view_all')
def accounts():
    account_form, category_form = AccountForm(prefix='konto'), CategoryForm(prefix='kst')
    if request.method == 'POST':
        if not can('finance.accounts.manage'):
            abort(403)
        try:
            if account_form.submit.data and account_form.validate():
                ledger.create_account(
                    account_form.name.data,
                    account_form.kind.data,
                    needs_cost_centre=account_form.needs_cost_centre.data,
                    actor=current_user,
                )
                message = 'Konto angelegt.'
            elif category_form.submit.data and category_form.validate():
                ledger.create_category(category_form.name.data, actor=current_user)
                message = 'Kostenstelle angelegt.'
            else:
                message = None
        except ledger.FinanceError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            if message:
                db.session.commit()
                flash(message, 'success')
                return redirect(url_for('finance.accounts'))
    balances = ledger.account_balances()
    return render_template(
        'finance/accounts.html',
        balances=balances,
        categories=ledger.categories(include_archived=True),
        category_totals=ledger.category_totals(),
        account_form=account_form,
        category_form=category_form,
        kind_labels=AccountKind.LABELS,
    )


@bp.route('/konten/<int:account_id>', methods=['GET', 'POST'])
@require_perm('finance.accounts.manage')
def edit_account(account_id):
    account = db.get_or_404(FinAccount, account_id)
    if account.kind == AccountKind.MEMBER:
        abort(404)
    form = AccountEditForm(obj=account)
    if request.method == 'GET':
        form.archived.data = account.archived_at is not None
    if form.validate_on_submit():
        try:
            diff = ledger.update_account(
                account,
                name=form.name.data,
                needs_cost_centre=form.needs_cost_centre.data,
                archived=form.archived.data,
                actor=current_user,
            )
        except ledger.FinanceError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash('Gespeichert' if diff else 'Keine Änderungen', 'success')
            return redirect(url_for('finance.accounts'))
    return render_template('finance/edit_account.html', account=account, form=form)


@bp.route('/kostenstellen/<int:category_id>', methods=['GET', 'POST'])
@require_perm('finance.accounts.manage')
def edit_category(category_id):
    category = db.get_or_404(FinCategory, category_id)
    form = CategoryEditForm(obj=category)
    if request.method == 'GET':
        form.archived.data = category.archived_at is not None
    if form.validate_on_submit():
        try:
            diff = ledger.update_category(
                category, name=form.name.data, archived=form.archived.data, actor=current_user
            )
        except ledger.FinanceError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash('Gespeichert' if diff else 'Keine Änderungen', 'success')
            return redirect(url_for('finance.accounts'))
    return render_template('finance/edit_category.html', category=category, form=form)


# --- Umlage: charge many members at once (M3-4) -----------------------------------------


def _target_choices():
    return [(a.id, ledger.account_label(a)) for a in ledger.accounts() if a.kind != AccountKind.MEMBER]


def _category_choices():
    return [('', '– keine –')] + [(str(c.id), c.name) for c in ledger.categories()]


def _rank_choices(key_values=False):
    return [(r.key if key_values else r.id, r.label) for r in org.ranks()]


def _group_choices():
    return [(0, '– keine –')] + [(g.id, g.name) for g in org.groups()]


@bp.route('/umlage', methods=['GET', 'POST'])
@require_perm('finance.journal.post')
def umlage():
    form = UmlageForm()
    form.target.choices = _target_choices()
    form.category.choices = _category_choices()
    form.rank_ids.choices = form.subsidy_rank_ids.choices = _rank_choices()
    form.group_id.choices = _group_choices()
    form.member_ids.choices = [(m.id, m.full_name) for m in _members_for_choices() if m.is_active_member]
    funds = [a for a in ledger.accounts([AccountKind.FUND])]
    form.subsidy_account.choices = [(0, '– kein Zuschuss –')] + [(a.id, ledger.account_label(a)) for a in funds]
    if request.method == 'GET':
        form.booked_on.data = today_local()
        form.beleg.data = ledger.next_beleg('B', form.booked_on.data)
        verschiedenes = ledger.account_by_key('verschiedenes')
        form.target.data = verschiedenes.id if verschiedenes else None
        fuxenkasse = ledger.account_by_key('fuxenkasse')
        fux = org.rank_by_key('fux')
        if fuxenkasse is not None and fux is not None:
            form.subsidy_rank_ids.data = [fux.id]
    shares, sums = None, None
    if form.validate_on_submit():
        try:
            day = form.booked_on.data
            group = db.session.get(MemberGroup, form.group_id.data) if form.group_id.data else None
            members = charges.audience(day, rank_ids=form.rank_ids.data, group=group, member_ids=form.member_ids.data)
            subsidy = None
            if form.subsidy_account.data:
                subsidy = charges.Subsidy(
                    db.session.get(FinAccount, form.subsidy_account.data),
                    frozenset(form.subsidy_rank_ids.data),
                    form.subsidy_amount.cents,
                )
            per_person = form.amount.cents if form.mode.data == 'person' else None
            total = form.amount.cents if form.mode.data == 'total' else None
            shares = charges.plan(members, day, per_person_cents=per_person, total_cents=total, subsidy=subsidy)
            sums = charges.totals(shares)
            if form.confirm.data and form.token.data:
                target = db.session.get(FinAccount, form.target.data)
                category = db.session.get(FinCategory, int(form.category.data)) if form.category.data else None
                journal, _ = charges.post(
                    shares,
                    title=form.title.data,
                    booked_on=day,
                    target=target,
                    category=category,
                    posted_by=current_user,
                    subsidy=subsidy,
                    beleg=form.beleg.data,
                    idempotency_key=f'umlage:{form.token.data}',
                )
                db.session.commit()
                flash(f'Umlage gebucht: {sums["people"]} Personen, {format_cents(sums["share"])}.', 'success')
                return redirect(url_for('finance.booking', journal_id=journal.id))
            form.token.data = secrets.token_urlsafe(16)
        except ledger.FinanceError as error:
            db.session.rollback()
            flash(str(error), 'danger')
            shares = None
    return render_template('finance/umlage.html', form=form, shares=shares, sums=sums)


# --- Monthly charges ----------------------------------------------------------------------------


def _recurring_form(recurring=None):
    form = RecurringForm(obj=recurring)
    form.target.choices = _target_choices()
    form.category.choices = _category_choices()
    form.rank_keys.choices = _rank_choices(key_values=True)
    form.group_id.choices = _group_choices()
    if request.method == 'GET' and recurring is not None:
        form.amount.data = format_cents(recurring.amount_cents).replace(' €', '')
        form.target.data = recurring.target_account_id
        form.category.data = str(recurring.category_id or '')
        form.rank_keys.data = recurring.rank_key_list
        form.group_id.data = recurring.group_id or 0
    return form


def _save_recurring(form, recurring):
    return charges.save_recurring(
        recurring,
        title=form.title.data,
        amount_cents=form.amount.cents,
        target=db.session.get(FinAccount, form.target.data),
        category=db.session.get(FinCategory, int(form.category.data)) if form.category.data else None,
        rank_keys=form.rank_keys.data,
        group=db.session.get(MemberGroup, form.group_id.data) if form.group_id.data else None,
        actor=current_user,
    )


def _month_arg():
    raw = request.args.get('monat', '')
    try:
        year, month = (int(part) for part in raw.split('-'))
        return date(year, month, 1)
    except ValueError:
        today = today_local()
        return date(today.year, today.month, 1)


@bp.route('/monatlich', methods=['GET', 'POST'])
@require_perm('finance.journal.post')
def recurring_list():
    form = _recurring_form()
    if form.validate_on_submit():
        try:
            item = _save_recurring(form, None)
        except ledger.FinanceError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash('Monatliche Belastung angelegt.', 'success')
            return redirect(url_for('finance.recurring', recurring_id=item.id))
    month = _month_arg()
    items = [(item, charges.recurring_posted(item, month)) for item in charges.recurring_items()]
    return render_template(
        'finance/recurring_list.html',
        items=items,
        form=form,
        month=month,
        month_label=charges.month_label(month),
        rank_labels={r.key: r.label for r in org.ranks()},
    )


@bp.route('/monatlich/<int:recurring_id>', methods=['GET', 'POST'])
@require_perm('finance.journal.post')
def recurring(recurring_id):
    item = db.get_or_404(FinRecurring, recurring_id)
    month = _month_arg()
    button_form = ButtonForm()
    if request.method == 'POST' and button_form.validate_on_submit():
        try:
            journal, created = charges.post_recurring(item, month, posted_by=current_user)
        except ledger.FinanceError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash('Gebucht.' if created else 'Dieser Monat war schon gebucht.', 'success' if created else 'info')
            return redirect(url_for('finance.booking', journal_id=journal.id))
    posted = charges.recurring_posted(item, month)
    try:
        shares = charges.recurring_plan(item, month) if posted is None else []
    except ledger.FinanceError:
        shares = []
    previous = date(month.year - (month.month == 1), (month.month - 2) % 12 + 1, 1)
    following = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return render_template(
        'finance/recurring.html',
        item=item,
        month=month,
        month_label=charges.month_label(month),
        previous=previous,
        following=following,
        posted=posted,
        shares=shares,
        sums=charges.totals(shares),
        button_form=button_form,
        edit_form=_recurring_form(item),
        rank_labels={r.key: r.label for r in org.ranks()},
    )


@bp.route('/monatlich/<int:recurring_id>/bearbeiten', methods=['POST'])
@require_perm('finance.journal.post')
def edit_recurring(recurring_id):
    item = db.get_or_404(FinRecurring, recurring_id)
    form = _recurring_form(item)
    if form.validate_on_submit():
        try:
            _save_recurring(form, item)
        except ledger.FinanceError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash('Gespeichert.', 'success')
    else:
        flash('Bitte die Angaben prüfen.', 'danger')
    return redirect(url_for('finance.recurring', recurring_id=item.id))


# --- Gegenzeichnung (push-TAN, M3-8) ---------------------------------------------------------


@bp.route('/gegenzeichnung')
@require_perm('finance.ledger.view_all')
def cosign_list():
    return render_template(
        'finance/cosign_list.html', open_items=cosign.open_requests(), decided=cosign.decided_requests()
    )


def _request_page(item, reject_form=None, status=200):
    try:
        lines = cosign.request_lines(item)
    except ledger.FinanceError as error:
        lines, problem = [], str(error)
    else:
        problem = None
    may_decide = (
        item.is_open and can('finance.journal.cosign') and item.requested_by.member_id != current_user.member_id
    )
    page = render_template(
        'finance/cosign_request.html',
        item=item,
        lines=lines,
        problem=problem,
        total=sum(line.amount_cents for line in lines),
        may_decide=may_decide,
        reject_form=reject_form or RejectForm(),
    )
    return page, status


@bp.route('/gegenzeichnung/<int:request_id>')
@require_perm('finance.ledger.view_all')
def cosign_request(request_id):
    return _request_page(db.get_or_404(FinRequest, request_id))


@bp.route('/gegenzeichnung/<int:request_id>/bestaetigen', methods=['POST'])
@require_perm('finance.journal.cosign')
def cosign_confirm(request_id):
    item = db.get_or_404(FinRequest, request_id)
    if not ButtonForm().validate_on_submit():
        abort(400)
    try:
        journal = cosign.confirm(item, confirmed_by=current_user)
    except ledger.FinanceError as error:
        db.session.rollback()
        flash(str(error), 'danger')
        return redirect(url_for('finance.cosign_request', request_id=item.id))
    db.session.commit()
    flash(f'Bestätigt und gebucht: {journal.title}', 'success')
    return redirect(url_for('finance.booking', journal_id=journal.id))


@bp.route('/gegenzeichnung/<int:request_id>/ablehnen', methods=['POST'])
@require_perm('finance.journal.cosign')
def cosign_reject(request_id):
    item = db.get_or_404(FinRequest, request_id)
    form = RejectForm()
    if not form.validate_on_submit():
        return _request_page(item, form, 400)
    try:
        cosign.reject(item, rejected_by=current_user, reason=form.reason.data)
    except ledger.FinanceError as error:
        db.session.rollback()
        flash(str(error), 'danger')
        return redirect(url_for('finance.cosign_request', request_id=item.id))
    db.session.commit()
    flash('Abgelehnt. Es wurde nichts gebucht.', 'success')
    return redirect(url_for('finance.cosign_list'))
