from flask import abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from community.core.platform import audit
from community.core.security import admin_required
from community.core.time import local_to_utc, utcnow
from community.extensions import db

from . import bp
from .forms import BookingForm, ButtonForm, ResourceForm
from .models import Booking, BookingStatus, Resource
from .services import BookingConflict, cancel_booking, create_booking


@bp.route('/bookings', methods=['GET', 'POST'])
@login_required
def calendar():
    resources = db.session.scalars(select(Resource).where(Resource.is_active).order_by(Resource.name)).all()
    form = BookingForm()
    form.resource_id.choices = [(r.id, r.name) for r in resources]
    if form.validate_on_submit():
        try:
            create_booking(
                resource_id=form.resource_id.data,
                user_id=current_user.id,
                start=local_to_utc(form.start.data),
                end=local_to_utc(form.end.data),
            )
        except BookingConflict:
            flash('Zeitraum überschneidet sich mit einer bestehenden Buchung', 'danger')
        except ValueError:
            flash('Das Ende muss nach dem Start liegen.', 'danger')
        else:
            flash('Gebucht', 'success')
            return redirect(url_for('bookings.calendar'))
    items = db.session.scalars(
        select(Booking)
        .where(Booking.status == BookingStatus.ACTIVE, Booking.end >= utcnow())
        .order_by(Booking.start)
        .limit(50)
    ).all()
    return render_template(
        'bookings/calendar.html', form=form, items=items, resources=resources, button_form=ButtonForm()
    )


@bp.route('/bookings/<int:booking_id>/cancel', methods=['POST'])
@login_required
def cancel(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    if not booking.can_cancel(current_user):
        abort(403)
    if ButtonForm().validate_on_submit():
        cancel_booking(booking)
        flash('Buchung storniert', 'success')
    return redirect(url_for('bookings.calendar'))


@bp.route('/bookings/resources', methods=['GET', 'POST'])
@admin_required
def resources():
    form = ResourceForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        if db.session.scalar(select(Resource).where(Resource.name == name)):
            flash('Diese Ressource gibt es schon', 'warning')
        else:
            resource = Resource(name=name)
            db.session.add(resource)
            db.session.flush()
            audit.record('resource.create', resource, diff={'name': name})
            db.session.commit()
            flash('Ressource angelegt', 'success')
            return redirect(url_for('bookings.resources'))
    items = db.session.scalars(select(Resource).order_by(Resource.name)).all()
    return render_template('bookings/resources.html', form=form, items=items, button_form=ButtonForm())


@bp.route('/bookings/resources/<int:resource_id>/toggle', methods=['POST'])
@admin_required
def toggle_resource(resource_id):
    resource = db.get_or_404(Resource, resource_id)
    if ButtonForm().validate_on_submit():
        resource.is_active = not resource.is_active
        audit.record('resource.toggle_active', resource, diff={'is_active': resource.is_active})
        db.session.commit()
    return redirect(url_for('bookings.resources'))
