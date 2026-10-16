"""Getränke pages: my Deckel and the Bierkönig for members; Strichliste, list, products and devices
for the Bierwart."""

import secrets
from datetime import date

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from community.core.authz import require_perm
from community.core.identity.models import Member, MemberStatus
from community.core.platform import api
from community.core.time import today_local
from community.extensions import db

from . import bp
from . import services as drinks
from .forms import ButtonForm, CorrectForm, DeviceForm, PriceForm, ProductEditForm, ProductForm, SheetForm
from .models import DrinkConsumption, DrinkProduct

# --- Members -----------------------------------------------------------------------------------


@bp.route('/')
@require_perm('drinks.view_own')
def mine():
    member = current_user.member
    month = drinks.month_range()
    semester = drinks.semester_range()
    return render_template(
        'drinks/mine.html',
        month=drinks.deckel(member, *month),
        semester=drinks.deckel(member, *semester),
        month_start=month[0],
        ranking=drinks.beer_ranking(*month),
        rows=drinks.recent(member=member, limit=30),
    )


# --- Bierwart: Strichliste -----------------------------------------------------------------------


def _sheet_members():
    return db.session.scalars(
        select(Member).where(Member.status == MemberStatus.ACTIVE).order_by(Member.last_name, Member.first_name)
    ).all()


def _read_counts(members, products):
    """{(member, product): quantity} from the q-<member>-<product> fields, and a list of errors."""
    counts, errors = {}, []
    for member in members:
        for product in products:
            raw = request.form.get(f'q-{member.id}-{product.id}', '').strip()
            if not raw:
                continue
            if not raw.isdigit() or not 0 <= int(raw) <= drinks.MAX_QUANTITY:
                errors.append(
                    f'{member.full_name}, {product.name}: „{raw}“ ist keine Anzahl von 0 bis {drinks.MAX_QUANTITY}.'
                )
                continue
            counts[(member, product)] = int(raw)
    return counts, errors


@bp.route('/strichliste', methods=['GET', 'POST'])
@require_perm('drinks.tally.record')
def sheet():
    form = SheetForm()
    members, products = _sheet_members(), drinks.products()
    if request.method == 'GET':
        form.day.data = request.args.get('tag', type=date.fromisoformat) or today_local()
        form.token.data = secrets.token_hex(8)
    errors = []
    if form.validate_on_submit():
        counts, errors = _read_counts(members, products)
        if not errors:
            try:
                result = drinks.record_sheet(
                    form.day.data, counts, token=form.token.data or secrets.token_hex(8), recorded_by=current_user
                )
            except drinks.DrinksError as error:
                db.session.rollback()
                errors = [str(error)]
            else:
                db.session.commit()
                if result.created:
                    flash(f'{result.created} Zeilen gespeichert.', 'success')
                if result.duplicates:
                    flash(f'{result.duplicates} Zeilen waren schon gespeichert.', 'info')
                if not result.created and not result.duplicates:
                    flash('Keine Striche eingetragen.', 'info')
                return redirect(url_for('drinks.sheet', tag=form.day.data.isoformat()))
    return render_template('drinks/sheet.html', form=form, members=members, products=products, errors=errors)


# --- Bierwart: list and corrections ------------------------------------------------------------


@bp.route('/liste')
@require_perm('drinks.view_all')
def all_entries():
    return render_template('drinks/list.html', rows=drinks.recent(limit=300), form=CorrectForm())


@bp.route('/<int:row_id>/korrigieren', methods=['POST'])
@require_perm('drinks.tally.record')
def correct(row_id):
    row = db.get_or_404(DrinkConsumption, row_id)
    form = CorrectForm()
    if not form.validate_on_submit():
        flash('Bitte einen Grund für die Korrektur angeben.', 'danger')
        return redirect(url_for('drinks.all_entries'))
    try:
        drinks.correct(row, corrected_by=current_user, reason=form.reason.data)
    except drinks.DrinksError as error:
        db.session.rollback()
        flash(str(error), 'danger')
    else:
        db.session.commit()
        flash('Korrigiert.', 'success')
    return redirect(url_for('drinks.all_entries'))


# --- Bierwart: products and prices -------------------------------------------------------------


@bp.route('/verwaltung', methods=['GET', 'POST'])
@require_perm('drinks.catalog.manage')
def catalog():
    form = ProductForm()
    if form.validate_on_submit():
        try:
            product = drinks.create_product(
                form.name.data,
                form.price.cents,
                counts_as_beer=form.counts_as_beer.data,
                volume_ml=form.volume_ml.data,
                actor=current_user,
            )
        except drinks.DrinksError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash(f'„{product.name}“ angelegt.', 'success')
            return redirect(url_for('drinks.catalog'))
    today = today_local()
    items = [(product, drinks.price_on(product, today)) for product in drinks.products(include_archived=True)]
    return render_template('drinks/catalog.html', form=form, items=items)


@bp.route('/verwaltung/<int:product_id>', methods=['GET', 'POST'])
@require_perm('drinks.catalog.manage')
def edit_product(product_id):
    product = db.get_or_404(DrinkProduct, product_id)
    form = ProductEditForm(prefix='p', obj=product)
    price_form = PriceForm(prefix='preis')
    if request.method == 'GET':
        form.archived.data = product.archived_at is not None
        price_form.valid_from.data = today_local()
    try:
        if form.submit.data and form.validate():
            diff = drinks.update_product(
                product,
                name=form.name.data,
                counts_as_beer=form.counts_as_beer.data,
                volume_ml=form.volume_ml.data,
                archived=form.archived.data,
                actor=current_user,
            )
            message = 'Gespeichert.' if diff else 'Keine Änderungen.'
        elif price_form.submit.data and price_form.validate():
            drinks.set_price(product, price_form.price.cents, price_form.valid_from.data, actor=current_user)
            message = 'Preis gespeichert.'
        else:
            message = None
    except drinks.DrinksError as error:
        db.session.rollback()
        flash(str(error), 'danger')
    else:
        if message:
            db.session.commit()
            flash(message, 'success')
            return redirect(url_for('drinks.edit_product', product_id=product.id))
    return render_template('drinks/edit_product.html', product=product, form=form, price_form=price_form)


# --- Devices (the bar tablet) ------------------------------------------------------------------


@bp.route('/geraete', methods=['GET', 'POST'])
@require_perm('drinks.devices.manage')
def devices():
    form = DeviceForm()
    form.scopes.choices = sorted(api.SCOPES.items())
    token = None
    if form.validate_on_submit():
        try:
            client, token = api.create_client(form.name.data, form.scopes.data, created_by=current_user)
        except api.ApiClientError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash(f'Gerät „{client.name}“ angelegt. Das Token wird nur jetzt angezeigt.', 'success')
    return render_template(
        'drinks/devices.html', form=form, clients=api.clients(), token=token, button_form=ButtonForm()
    )


@bp.route('/geraete/<int:client_id>/sperren', methods=['POST'])
@require_perm('drinks.devices.manage')
def revoke_device(client_id):
    client = db.get_or_404(api.ApiClient, client_id)
    if not ButtonForm().validate_on_submit():
        abort(400)
    api.revoke_client(client, revoked_by=current_user)
    db.session.commit()
    flash(f'„{client.name}“ ist gesperrt.', 'success')
    return redirect(url_for('drinks.devices'))
