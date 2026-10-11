from flask import flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from community.core.identity.models import User
from community.core.time import local_to_utc
from community.extensions import db

from . import bp
from .forms import BookingForm
from .models import Booking, Resource


@bp.route('/bookings', methods=['GET', 'POST'])
@login_required
def calendar():
    form = BookingForm()
    if form.validate_on_submit():
        # ensure resource exists
        r = db.session.scalar(select(Resource).where(Resource.name == form.resource.data))
        if not r:
            r = Resource(name=form.resource.data)
            db.session.add(r)
            db.session.commit()
        start, end = local_to_utc(form.start.data), local_to_utc(form.end.data)
        b = Booking(resource_id=r.id, user_id=current_user.id, start=start, end=end)
        # basic rule: no overlap
        overlap = (
            db.session.execute(
                select(Booking).where(
                    Booking.resource_id == r.id,
                    Booking.end > start,
                    Booking.start < end,
                )
            )
            .scalars()
            .first()
        )
        if overlap:
            flash('Zeitraum überschneidet sich mit bestehender Buchung', 'danger')
        else:
            db.session.add(b)
            db.session.commit()
            flash('Gebucht', 'success')
            return redirect(url_for('bookings.calendar'))
    # show recent
    items = db.session.execute(
        select(Booking, Resource, User)
        .join(Resource, Booking.resource_id == Resource.id)
        .join(User, Booking.user_id == User.id)
        .order_by(Booking.start.desc())
        .limit(20)
    ).all()
    return render_template('bookings/calendar.html', form=form, items=items)
