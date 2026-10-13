"""Kasse pages: the member's own Kontoauszug, and the Kassenwart's workspace.

Only the Kassenwart and the Kassenprüfer see the whole Kasse (finance.ledger.view_all, Q22);
every member sees their own Saldo (finance.saldo.view_own).
"""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_, select

from community.core.authz import can, require_perm
from community.core.identity.models import Member, MemberStatus
from community.core.time import today_local
from community.core.widgets import profile_card
from community.extensions import db

from . import bp
from . import services as ledger
from .forms import (
    AccountEditForm,
    AccountForm,
    BookingForm,
    CategoryEditForm,
    CategoryForm,
    ReverseForm,
)
from .models import AccountKind, FinAccount, FinCategory, FinJournal

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
    return render_template('finance/booking.html', item=item, form=ReverseForm())


@bp.route('/buchung/<int:journal_id>/storno', methods=['POST'])
@require_perm('finance.journal.reverse')
def reverse(journal_id):
    item = db.get_or_404(FinJournal, journal_id)
    form = ReverseForm()
    if not form.validate_on_submit():
        flash('Bitte einen Grund für das Storno angeben.', 'danger')
        return redirect(url_for('finance.booking', journal_id=item.id))
    try:
        reversal = ledger.reverse(item, reversed_by=current_user, reason=form.reason.data)
    except ledger.FinanceError as error:
        db.session.rollback()
        flash(str(error), 'danger')
        return redirect(url_for('finance.booking', journal_id=item.id))
    db.session.commit()
    flash('Storniert.', 'success')
    return redirect(url_for('finance.booking', journal_id=reversal.id))


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
    if form.validate_on_submit():
        debit, credit = _resolve(form.debit.data), _resolve(form.credit.data)
        category = db.session.get(FinCategory, int(form.category.data)) if form.category.data else None
        try:
            journal, _ = ledger.post_journal(
                ledger.kind_for(debit, credit),
                form.title.data,
                form.booked_on.data,
                [ledger.Line(debit, credit, form.amount.cents, category)],
                posted_by=current_user,
                beleg=form.beleg.data,
                note=form.note.data,
            )
        except ledger.FinanceError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash(f'Gebucht: {journal.title}', 'success')
            if request.form.get('weiter'):
                return redirect(url_for('finance.new_booking'))
            return redirect(url_for('finance.booking', journal_id=journal.id))
    day = form.booked_on.data or today_local()
    next_numbers = [ledger.next_beleg(series, day) for series in BELEG_SERIES]
    return render_template('finance/new_booking.html', form=form, next_numbers=next_numbers, member=member)


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
