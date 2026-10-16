"""Start page card of the bookings: what is booked in the house in the next days."""

from datetime import timedelta

from flask_login import current_user
from sqlalchemy import select

from community.core.time import utcnow
from community.core.widgets import MAIN, home_card
from community.extensions import db

from .models import Booking, BookingStatus

DAYS = 14
LIMIT = 5


@home_card('bookings/_home_upcoming.html', permission='bookings.create_own', order=40, area=MAIN)
def upcoming_card(member):
    now = utcnow()
    items = db.session.scalars(
        select(Booking)
        .where(Booking.status == BookingStatus.ACTIVE, Booking.end >= now, Booking.start <= now + timedelta(days=DAYS))
        .order_by(Booking.start)
        .limit(LIMIT)
    ).all()
    return {'items': items, 'me': current_user.id} if items else None
