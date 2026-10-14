"""M3-8: push-TAN. A booking that credits the Kassenwart's own Kontokarte waits for the Kassenprüfer."""

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from community.core.org.models import OfficeAssignment
from community.core.org.services import end_assignment, office_by_key
from community.core.platform.audit import AuditLog
from community.core.platform.notify import Notification
from community.core.time import today_local
from community.extensions import db
from community.modules.finance import cosign
from community.modules.finance.models import FinCategory, FinJournal, FinRequest, JournalKind, RequestStatus
from community.modules.finance.services import (
    FinanceError,
    Line,
    account_by_key,
    member_account,
    post_journal,
    saldo,
    update_account,
)

from .factories import DEFAULT_PASSWORD, make_admin, make_member, make_officer, make_user

DAY = date(2026, 10, 6)


def journal_count():
    return db.session.scalar(select(func.count()).select_from(FinJournal))


def request_count():
    return db.session.scalar(select(func.count()).select_from(FinRequest))


def kneipen():
    return db.session.scalar(select(FinCategory).where(FinCategory.name == 'Kneipen'))


def own_auslage(kassenwart, **overrides):
    """The booking form for an Auslage the Kassenwart paid themselves (credits their own card)."""
    data = {
        'booked_on': DAY.isoformat(),
        'title': 'Auslage Einkauf',
        'amount': '30',
        'debit': f'a{account_by_key("verschiedenes").id}',
        'credit': f'm{kassenwart.member_id}',
        'category': str(kneipen().id),
    }
    data.update(overrides)
    return data


def client_for(app, user):
    """A separate browser, logged in as `user`."""
    other = app.test_client()
    other.post('/login', data={'email': user.login_email, 'password': DEFAULT_PASSWORD})
    return other


def notifications(user, kind):
    query = select(Notification).where(Notification.member_id == user.member_id, Notification.kind == kind)
    return db.session.scalars(query).all()


def audit_actions():
    return set(db.session.scalars(select(AuditLog.action)))


@pytest.fixture
def officers(app):
    return make_officer('kassenwart'), make_officer('kassenpruefer')


def submit(client, kassenwart):
    response = client.post('/kasse/buchung/neu', data={**own_auslage(kassenwart), 'vorlegen': '1'})
    assert response.status_code == 302, response.get_data(as_text=True)
    return response


# --- The whole flow -------------------------------------------------------------------------


def test_kassenwart_submits_and_kassenpruefer_confirms_once(app, client, login, officers):
    kassenwart, pruefer = officers
    login(kassenwart)
    page = client.post('/kasse/buchung/neu', data=own_auslage(kassenwart)).get_data(as_text=True)
    assert 'Zur Gegenzeichnung vorlegen' in page and journal_count() == 0

    response = submit(client, kassenwart)
    assert '/kasse/gegenzeichnung/' in response.location
    submit(client, kassenwart)  # a double click prepares nothing new
    assert request_count() == 1 and journal_count() == 0
    item = db.session.scalar(select(FinRequest))
    assert item.status == RequestStatus.OPEN and item.requested_by_user_id == kassenwart.id
    assert len(notifications(pruefer, cosign.REQUEST_KIND)) == 1
    assert notifications(kassenwart, cosign.REQUEST_KIND) == []

    page = client.get(f'/kasse/gegenzeichnung/{item.id}').get_data(as_text=True)
    assert 'Noch nicht gebucht' in page and '30,00 €' in page and 'Bestätigen und buchen' not in page
    # The Kassenwart cannot confirm: they lack the right, whatever they send.
    assert client.post(f'/kasse/gegenzeichnung/{item.id}/bestaetigen').status_code == 403
    assert client.post(f'/kasse/gegenzeichnung/{item.id}/ablehnen', data={'reason': 'x'}).status_code == 403

    other = client_for(app, pruefer)
    assert 'Bestätigen und buchen' in other.get(f'/kasse/gegenzeichnung/{item.id}').get_data(as_text=True)
    assert '1 Buchung wartet auf Gegenzeichnung' in other.get('/kasse/').get_data(as_text=True)
    response = other.post(f'/kasse/gegenzeichnung/{item.id}/bestaetigen')
    assert response.status_code == 302 and '/kasse/buchung/' in response.location
    other.post(f'/kasse/gegenzeichnung/{item.id}/bestaetigen')  # a second click books nothing
    assert journal_count() == 1

    db.session.expire_all()
    journal = db.session.scalar(select(FinJournal))
    assert (journal.posted_by_user_id, journal.cosigned_by_user_id) == (kassenwart.id, pruefer.id)
    assert journal.kind == JournalKind.EXPENSE and journal.idempotency_key == f'request:{item.id}'
    assert saldo(kassenwart.member) == 3000
    assert item.status == RequestStatus.CONFIRMED and item.journal_id == journal.id
    assert len(notifications(kassenwart, cosign.DECIDED_KIND)) == 1
    assert {'finance.request.create', 'finance.request.confirm', 'finance.journal.post'} <= audit_actions()
    page = other.get(f'/kasse/buchung/{journal.id}').get_data(as_text=True)
    assert 'Gegengezeichnet' in page and 'Kassenpruefer Person' in page


def test_reject_books_nothing_and_needs_a_reason(app, client, login, officers):
    kassenwart, pruefer = officers
    login(kassenwart)
    submit(client, kassenwart)
    item = db.session.scalar(select(FinRequest))

    other = client_for(app, pruefer)
    response = other.post(f'/kasse/gegenzeichnung/{item.id}/ablehnen', data={'reason': ''})
    assert response.status_code == 400
    response = other.post(f'/kasse/gegenzeichnung/{item.id}/ablehnen', data={'reason': 'Beleg fehlt'})
    assert response.status_code == 302
    other.post(f'/kasse/gegenzeichnung/{item.id}/bestaetigen')  # too late: already decided
    db.session.expire_all()
    assert item.status == RequestStatus.REJECTED and item.reject_reason == 'Beleg fehlt'
    assert journal_count() == 0 and saldo(kassenwart.member) == 0
    (note,) = notifications(kassenwart, cosign.DECIDED_KIND)
    assert 'Beleg fehlt' in note.title
    assert 'finance.request.reject' in audit_actions()
    page = other.get('/kasse/gegenzeichnung').get_data(as_text=True)
    assert 'Nichts wartet auf Gegenzeichnung' in page and 'abgelehnt' in page


# --- Invariants of the service ----------------------------------------------------------------


def prepared(kassenwart, debit=None):
    item, created = cosign.request_booking(
        JournalKind.EXPENSE,
        'Auslage',
        DAY,
        [Line(debit or account_by_key('verschiedenes'), member_account(kassenwart.member), 2500, kneipen())],
        requested_by=kassenwart,
    )
    assert created
    return item


def test_nobody_confirms_their_own_request(app, officers):
    kassenwart, _ = officers
    with app.test_request_context():
        item = prepared(kassenwart)
        with pytest.raises(FinanceError, match='andere Person'):
            cosign.confirm(item, confirmed_by=kassenwart)
        with pytest.raises(FinanceError, match='andere Person'):
            cosign.reject(item, rejected_by=kassenwart, reason='doch nicht')
    assert journal_count() == 0 and item.is_open


def test_a_member_without_the_right_cannot_confirm(app, officers):
    kassenwart, _ = officers
    someone = make_user('other@example.org', name='Otto Ohne')
    with app.test_request_context():
        item = prepared(kassenwart)
        with pytest.raises(FinanceError, match='keine Buchungen gegenzeichnen'):
            cosign.confirm(item, confirmed_by=someone)
    assert journal_count() == 0


def test_a_request_is_only_for_bookings_that_need_it(app, officers):
    kassenwart, _ = officers
    member = make_member('Max Muster')
    with app.test_request_context(), pytest.raises(FinanceError, match='keine Gegenzeichnung'):
        cosign.request_booking(
            JournalKind.PAYMENT,
            'Zahlung',
            DAY,
            [Line(account_by_key('giro'), member_account(member), 1000)],
            requested_by=kassenwart,
        )
    with app.test_request_context(), pytest.raises(FinanceError, match='Kostenstelle'):
        cosign.request_booking(
            JournalKind.EXPENSE,
            'Auslage',
            DAY,
            [Line(account_by_key('verschiedenes'), member_account(kassenwart.member), 1000)],
            requested_by=kassenwart,
        )
    assert request_count() == 0


def test_without_a_kassenpruefer_nothing_can_be_submitted(client, login):
    kassenwart = make_officer('kassenwart')
    login(kassenwart)
    page = client.post(
        '/kasse/buchung/neu', data={**own_auslage(kassenwart), 'vorlegen': '1'}, follow_redirects=True
    ).get_data(as_text=True)
    assert 'Kassenprüfer eintragen' in page and request_count() == 0


def test_confirm_rechecks_the_requester_and_the_accounts(app, officers):
    kassenwart, pruefer = officers
    with app.test_request_context():
        item = prepared(kassenwart, debit=account_by_key('buero'))
        update_account(account_by_key('buero'), name='Büro', needs_cost_centre=False, archived=True, actor=None)
        with pytest.raises(FinanceError, match='archiviert'):
            cosign.confirm(item, confirmed_by=pruefer)
        db.session.rollback()

        assignment = db.session.scalar(
            select(OfficeAssignment).where(OfficeAssignment.office_id == office_by_key('kassenwart').id)
        )
        end_assignment(assignment, today_local() - timedelta(days=1))
        db.session.commit()
        with pytest.raises(FinanceError, match='darf nicht mehr buchen'):
            cosign.confirm(item, confirmed_by=pruefer)
    assert journal_count() == 0 and item.is_open


# --- Reversal of a charge on the Kassenwart's own card -------------------------------------


def test_reversing_own_charge_goes_through_the_kassenpruefer(app, client, login, officers):
    kassenwart, pruefer = officers
    journal, _ = post_journal(
        JournalKind.CHARGE,
        'Kneipe',
        DAY,
        [Line(member_account(kassenwart.member), account_by_key('verschiedenes'), 1250, kneipen())],
        posted_by=None,
    )
    db.session.commit()
    login(kassenwart)
    page = client.post(f'/kasse/buchung/{journal.id}/storno', data={'reason': 'doppelt'}).get_data(as_text=True)
    assert 'Zur Gegenzeichnung vorlegen' in page and journal_count() == 1

    response = client.post(f'/kasse/buchung/{journal.id}/storno', data={'reason': 'doppelt', 'vorlegen': '1'})
    assert response.status_code == 302 and '/kasse/gegenzeichnung/' in response.location
    page = client.get(f'/kasse/buchung/{journal.id}').get_data(as_text=True)
    assert 'wartet auf Gegenzeichnung' in page and 'name="reason"' not in page

    item = db.session.scalar(select(FinRequest))
    response = client_for(app, pruefer).post(f'/kasse/gegenzeichnung/{item.id}/bestaetigen')
    assert response.status_code == 302
    db.session.expire_all()
    assert saldo(kassenwart.member) == 0 and journal.reversed_by is not None
    assert journal.reversed_by.cosigned_by_user_id == pruefer.id and journal.reversed_by.note == 'doppelt'


# --- Who sees the pages -----------------------------------------------------------------------


def test_only_kasse_officers_see_the_requests(client, login, officers):
    login(make_admin())
    assert client.get('/kasse/gegenzeichnung').status_code == 403


def test_a_plain_member_gets_403(client, login):
    login(make_user('plain@example.org'))
    assert client.get('/kasse/gegenzeichnung').status_code == 403
