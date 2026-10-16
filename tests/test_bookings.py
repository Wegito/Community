from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from community.core.time import to_local, utcnow
from community.extensions import db
from community.modules.bookings.models import Booking, BookingStatus, Resource
from community.modules.bookings.services import BookingConflict, create_booking

from .factories import make_admin, make_user


def make_resource(name='Saal', active=True):
    resource = Resource(name=name, is_active=active)
    db.session.add(resource)
    db.session.commit()
    return resource


def form_time(value):
    """Format an aware datetime the way the browser's datetime-local input sends it (Berlin time)."""
    return to_local(value).strftime('%Y-%m-%dT%H:%M')


def tomorrow(hour):
    base = (utcnow() + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    return base.replace(hour=hour)


def test_booking_is_stored_in_utc_from_berlin_input(client, login):
    user = make_user()
    saal = make_resource()
    login(user)
    response = client.post(
        '/bookings', data={'resource_id': saal.id, 'start': '2030-07-01T20:00', 'end': '2030-07-01T23:00'}
    )
    assert response.status_code == 302
    booking = db.session.scalar(select(Booking))
    assert booking.start == datetime(2030, 7, 1, 18, 0, tzinfo=UTC)


def test_end_must_be_after_start(client, login):
    user = make_user()
    saal = make_resource()
    login(user)
    response = client.post(
        '/bookings', data={'resource_id': saal.id, 'start': '2030-07-01T20:00', 'end': '2030-07-01T19:00'}
    )
    assert 'Das Ende muss nach dem Start liegen' in response.get_data(as_text=True)
    assert db.session.scalar(select(Booking)) is None


def test_database_rejects_end_before_start(app):
    user = make_user()
    saal = make_resource()
    start = tomorrow(10)
    db.session.add(Booking(resource_id=saal.id, user_id=user.id, start=start, end=start))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_overlap_is_rejected_but_adjacent_is_fine(app):
    user = make_user()
    saal = make_resource()
    create_booking(saal.id, user.id, tomorrow(10), tomorrow(12))
    with pytest.raises(BookingConflict):
        create_booking(saal.id, user.id, tomorrow(11), tomorrow(13))
    create_booking(saal.id, user.id, tomorrow(12), tomorrow(14))
    other = make_resource('Beamer')
    create_booking(other.id, user.id, tomorrow(11), tomorrow(13))
    assert len(db.session.scalars(select(Booking)).all()) == 3


def test_cancelled_booking_frees_the_slot(client, login):
    user = make_user()
    saal = make_resource()
    booking = create_booking(saal.id, user.id, tomorrow(10), tomorrow(12))
    login(user)
    assert client.post(f'/bookings/{booking.id}/cancel').status_code == 302
    db.session.refresh(booking)
    assert booking.status == BookingStatus.CANCELLED
    create_booking(saal.id, user.id, tomorrow(10), tomorrow(12))


def test_only_owner_or_admin_cancels(client, login):
    owner = make_user()
    make_user(email='moritz@example.org', name='Moritz')
    saal = make_resource()
    booking = create_booking(saal.id, owner.id, tomorrow(10), tomorrow(12))
    login('moritz@example.org')
    assert client.post(f'/bookings/{booking.id}/cancel').status_code == 403
    assert client.post('/bookings/999/cancel').status_code == 404


def test_free_text_resource_is_not_created(client, login):
    make_user()
    login('max@example.org')
    client.post('/bookings', data={'resource_id': '42', 'start': '2030-07-01T20:00', 'end': '2030-07-01T21:00'})
    assert db.session.scalar(select(Resource)) is None
    assert db.session.scalar(select(Booking)) is None


def test_inactive_resource_cannot_be_booked(client, login):
    make_user()
    old = make_resource('Alt', active=False)
    login('max@example.org')
    client.post('/bookings', data={'resource_id': old.id, 'start': '2030-07-01T20:00', 'end': '2030-07-01T21:00'})
    assert db.session.scalar(select(Booking)) is None


def test_list_shows_names_not_emails(client, login):
    user = make_user()
    saal = make_resource()
    create_booking(saal.id, user.id, tomorrow(10), tomorrow(12))
    login(user)
    page = client.get('/bookings').get_data(as_text=True)
    assert 'von Max' in page
    assert 'max@example.org' not in page


def test_admin_manages_resources(client, login):
    make_admin()
    login('admin@example.org')
    client.post('/bookings/resources', data={'name': ' Saal '})
    saal = db.session.scalar(select(Resource))
    assert saal.name == 'Saal'
    client.post(f'/bookings/resources/{saal.id}/toggle')
    db.session.refresh(saal)
    assert saal.is_active is False


def test_member_cannot_manage_resources(client, login):
    make_user()
    login('max@example.org')
    client.post('/bookings/resources', data={'name': 'Saal'})
    assert db.session.scalar(select(Resource)) is None


def test_overlap_check_runs_under_the_write_lock(app):
    """While create_booking checks for overlaps, no other connection can write."""
    import sqlite3

    from sqlalchemy import event

    user = make_user()
    saal = make_resource()
    outcomes = []

    def probe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT') and 'FROM booking' in statement and not outcomes:
            other = sqlite3.connect(db.engine.url.database, timeout=0)
            try:
                other.execute('BEGIN IMMEDIATE')
                outcomes.append('acquired')
                other.rollback()
            except sqlite3.OperationalError as error:
                outcomes.append(str(error))
            finally:
                other.close()

    event.listen(db.engine, 'before_cursor_execute', probe)
    try:
        create_booking(saal.id, user.id, tomorrow(10), tomorrow(12))
    finally:
        event.remove(db.engine, 'before_cursor_execute', probe)
    assert outcomes == ['database is locked']


@pytest.mark.parametrize(
    ('start', 'end'),
    [('2027-03-28T02:30', '2027-03-28T03:15'), ('2027-03-28T02:00', '2027-03-28T03:00')],
)
def test_times_skipped_by_the_clock_change_are_rejected(client, login, start, end):
    """2027-03-28 02:00-03:00 does not exist in Berlin; this used to give a 500 error."""
    make_user()
    saal = make_resource()
    login('max@example.org')
    response = client.post('/bookings', data={'resource_id': saal.id, 'start': start, 'end': end})
    assert response.status_code == 200
    assert 'Zeitumstellung' in response.get_data(as_text=True)
    assert db.session.scalar(select(Booking)) is None


def test_booking_across_the_clock_change_works(client, login):
    make_user()
    saal = make_resource()
    login('max@example.org')
    response = client.post(
        '/bookings', data={'resource_id': saal.id, 'start': '2027-03-28T01:00', 'end': '2027-03-28T04:00'}
    )
    assert response.status_code == 302
    booking = db.session.scalar(select(Booking))
    assert booking.end - booking.start == timedelta(hours=2)
