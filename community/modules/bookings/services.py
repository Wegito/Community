"""Booking rules. Routes call these; they own the transaction."""

from sqlalchemy import select, update

from community.core.platform import audit
from community.extensions import db

from .models import Booking, BookingStatus, Resource


class BookingConflict(Exception):
    """The resource is already booked for (part of) the requested time."""


def create_booking(resource_id, user_id, start, end):
    """Book a resource if the time is free. `start`/`end` are aware datetimes, end > start."""
    if end <= start:
        raise ValueError('end must be after start')
    # Take the write lock before checking, so two simultaneous requests cannot both
    # pass the overlap check. On SQLite this UPDATE starts the write transaction
    # (others wait up to busy_timeout); on Postgres it locks the resource row.
    db.session.execute(
        update(Resource).where(Resource.id == resource_id).values(is_active=Resource.is_active),
        execution_options={'synchronize_session': False},
    )
    overlap = db.session.scalar(
        select(Booking.id)
        .where(
            Booking.resource_id == resource_id,
            Booking.status == BookingStatus.ACTIVE,
            Booking.end > start,
            Booking.start < end,
        )
        .limit(1)
    )
    if overlap is not None:
        db.session.rollback()
        raise BookingConflict
    booking = Booking(resource_id=resource_id, user_id=user_id, start=start, end=end)
    db.session.add(booking)
    db.session.flush()
    audit.record('booking.create', booking, diff={'resource_id': resource_id, 'start': start, 'end': end})
    db.session.commit()
    return booking


def cancel_booking(booking):
    booking.status = BookingStatus.CANCELLED
    audit.record('booking.cancel', booking)
    db.session.commit()
