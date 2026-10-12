import uuid
from decimal import Decimal

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from community.core.authz import can, require_perm
from community.core.money import format_cents
from community.core.platform import audit
from community.core.platform.qr import QrToken, qr_handler, qr_svg, rotate, tokens_for
from community.core.time import today_local, utcnow
from community.extensions import db

from . import bp, services
from .forms import ButtonForm, MachineStatusForm, PriceForm, UseForm, VoidForm
from .models import LaundryMachine, LaundryPrice, LaundryUse, MachineStatus, PriceKind, PriceTier

MONTH_NAMES = (
    'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
    'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember',
)  # fmt: skip


@qr_handler('laundry')
def _laundry_qr(qr):
    return url_for('laundry.new')


def month_context(year, month):
    prev, nxt = services.shift_month(year, month, -1), services.shift_month(year, month, 1)
    return {
        'year': year,
        'month': month,
        'month_label': f'{MONTH_NAMES[month - 1]} {year}',
        'month_key': f'{year:04d}-{month:02d}',
        'prev_key': f'{prev[0]:04d}-{prev[1]:02d}',
        'next_key': f'{nxt[0]:04d}-{nxt[1]:02d}',
    }


@bp.route('/neu', methods=['GET', 'POST'])
@require_perm('laundry.use.create_own')
def new():
    form = UseForm()
    if request.method == 'GET':
        form.submission_id.data = uuid.uuid4().hex
    if form.validate_on_submit():
        try:
            use, created = services.record_use(
                current_user.member,
                washes=form.washes.data,
                dried=form.dried.data,
                dry_program=form.dry_program.data or None,
                container=form.container.data,
                submission_id=form.submission_id.data or uuid.uuid4().hex,
                recorded_by=current_user,
            )
        except services.LaundryError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            if created:
                flash(f'Eingetragen: {_describe(use)} · {format_cents(use.total_cents)}', 'success')
            else:
                flash('Dieser Eintrag war schon gespeichert.', 'info')
            return redirect(url_for('laundry.mine'))
    tier = services.tier_for(current_user.member)
    prices = services.current_prices()
    return render_template(
        'laundry/new.html',
        form=form,
        tier_label=PriceTier.LABELS[tier],
        wash_price=prices.get((tier, PriceKind.WASH)),
        dry_price=prices.get((tier, PriceKind.DRY)),
        defects=services.defect_machines(),
    )


def _describe(use):
    parts = []
    if use.washes:
        parts.append('eine Maschine' if use.washes == 1 else 'beide Maschinen')
    if use.dried:
        parts.append(f'Trockner ({use.dry_program})')
    return ' + '.join(parts)


@bp.route('/')
@require_perm('laundry.view_own')
def mine():
    year, month = services.parse_month(request.args.get('monat'))
    uses = services.uses_in_month(year, month, member=current_user.member)
    total = sum(u.total_cents for u in uses if not u.is_void)
    now = utcnow()
    return render_template(
        'laundry/mine.html',
        uses=uses,
        total=total,
        undoable={u.id for u in uses if services.can_undo(u, current_user, now)},
        button_form=ButtonForm(),
        describe=_describe,
        **month_context(year, month),
    )


@bp.route('/<int:use_id>/rueckgaengig', methods=['POST'])
@require_perm('laundry.use.create_own')
def undo(use_id):
    use = db.get_or_404(LaundryUse, use_id)
    if not services.can_undo(use, current_user):
        abort(403)
    if ButtonForm().validate_on_submit():
        services.void_use(use, by=current_user, reason='selbst zurückgenommen')
        db.session.commit()
        flash('Eintrag zurückgenommen.', 'success')
    return redirect(url_for('laundry.mine'))


@bp.route('/abrechnung')
@require_perm('laundry.view_all')
def overview():
    year, month = services.parse_month(request.args.get('monat'))
    rows, totals = services.monthly_summary(year, month)
    return render_template(
        'laundry/overview.html', rows=rows, totals=totals, tier_labels=PriceTier.LABELS, **month_context(year, month)
    )


@bp.route('/abrechnung.xlsx')
@require_perm('laundry.view_all')
def overview_xlsx():
    year, month = services.parse_month(request.args.get('monat'))
    data = services.export_month_xlsx(year, month)
    filename = f'waesche-{year:04d}-{month:02d}.xlsx'
    return Response(
        data,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@bp.route('/liste')
@require_perm('laundry.view_all')
def all_entries():
    year, month = services.parse_month(request.args.get('monat'))
    return render_template(
        'laundry/list.html',
        uses=services.uses_in_month(year, month),
        void_form=VoidForm(),
        describe=_describe,
        may_void=can('laundry.machines.manage'),
        **month_context(year, month),
    )


@bp.route('/<int:use_id>/stornieren', methods=['POST'])
@require_perm('laundry.machines.manage')
def void(use_id):
    use = db.get_or_404(LaundryUse, use_id)
    form = VoidForm()
    if form.validate_on_submit():
        services.void_use(use, by=current_user, reason=form.reason.data)
        db.session.commit()
        flash('Eintrag storniert.', 'success')
    return redirect(url_for('laundry.all_entries', monat=request.form.get('monat') or None))


@bp.route('/verwaltung', methods=['GET', 'POST'])
@require_perm('laundry.machines.manage')
def settings():
    price_form = PriceForm(prefix='price')
    if request.method == 'GET':
        price_form.valid_from.data = today_local()
    if price_form.submit.data and price_form.validate_on_submit():
        cents = int((price_form.price.data * 100).quantize(Decimal('1')))
        existing = db.session.scalar(
            select(LaundryPrice).where(
                LaundryPrice.tier == price_form.tier.data,
                LaundryPrice.kind == price_form.kind.data,
                LaundryPrice.valid_from == price_form.valid_from.data,
            )
        )
        price = existing or LaundryPrice(
            tier=price_form.tier.data, kind=price_form.kind.data, valid_from=price_form.valid_from.data
        )
        price.price_cents = cents
        price.set_by_user_id = current_user.id
        db.session.add(price)
        db.session.flush()
        audit.record(
            'laundry.price',
            price,
            diff={'tier': price.tier, 'kind': price.kind, 'cents': cents, 'valid_from': price.valid_from},
        )
        db.session.commit()
        flash('Preis gespeichert.', 'success')
        return redirect(url_for('laundry.settings'))
    machines = db.session.scalars(select(LaundryMachine).order_by(LaundryMachine.sort_order)).all()
    prices = db.session.scalars(
        select(LaundryPrice).order_by(LaundryPrice.tier, LaundryPrice.kind, LaundryPrice.valid_from.desc())
    ).all()
    return render_template(
        'laundry/settings.html',
        machines=machines,
        prices=prices,
        price_form=price_form,
        status_form=MachineStatusForm(),
        button_form=ButtonForm(),
        status_labels=MachineStatus.LABELS,
        tier_labels=PriceTier.LABELS,
        kind_labels=PriceKind.LABELS,
        current=services.current_prices(),
        codes=tokens_for('laundry'),
    )


@bp.route('/verwaltung/maschinen/<int:machine_id>', methods=['POST'])
@require_perm('laundry.machines.manage')
def machine_status(machine_id):
    machine = db.get_or_404(LaundryMachine, machine_id)
    form = MachineStatusForm()
    if form.validate_on_submit():
        old = machine.status
        machine.status = form.status.data
        audit.record('laundry.machine', machine, diff={'status': [old, machine.status]})
        db.session.commit()
        flash(f'{machine.name}: {MachineStatus.LABELS[machine.status]}', 'success')
    return redirect(url_for('laundry.settings'))


@bp.route('/verwaltung/qr/<int:qr_id>')
@require_perm('laundry.machines.manage')
def qr_sheet(qr_id):
    qr = db.get_or_404(QrToken, qr_id)
    if qr.target_type != 'laundry':
        abort(404)
    from community.core.platform.mail import absolute_url

    link = absolute_url('qr.resolve', token=qr.token)
    return render_template('laundry/qr_sheet.html', qr=qr, link=link, svg=qr_svg(link, title=qr.label, scale=10))


@bp.route('/verwaltung/qr/<int:qr_id>/neu', methods=['POST'])
@require_perm('laundry.machines.manage')
def qr_rotate(qr_id):
    qr = db.get_or_404(QrToken, qr_id)
    if qr.target_type != 'laundry':
        abort(404)
    if ButtonForm().validate_on_submit():
        rotate(qr)
        audit.record('qr.rotate', qr, diff={'label': qr.label})
        db.session.commit()
        flash('Neuer QR-Code erzeugt. Bitte den neuen Aushang drucken; der alte funktioniert nicht mehr.', 'success')
    return redirect(url_for('laundry.qr_sheet', qr_id=qr.id))
