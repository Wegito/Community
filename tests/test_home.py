"""M2-1: the start page is built from cards that modules register; each shows only what the user may see."""

from datetime import date, timedelta

import pytest

from community.core import widgets
from community.core.org.services import add_to_group, create_semester, groups
from community.core.platform.notify import notify
from community.core.time import today_local, utcnow
from community.extensions import db
from community.modules.announcements.models import Announcement
from community.modules.bookings.models import Resource
from community.modules.bookings.services import create_booking
from community.modules.finance import charges
from community.modules.finance.models import JournalKind
from community.modules.finance.services import Line, account_by_key, member_account, post_journal
from community.modules.laundry.services import record_use
from community.modules.tasks.models import Chore

from .factories import make_member, make_officer, make_user, set_rank


def page(client, path='/'):
    response = client.get(path)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def charge(member, cents, title='Kneipe'):
    post_journal(
        JournalKind.CHARGE,
        title,
        today_local(),
        [Line(member_account(member), account_by_key('beitraege'), cents)],
        posted_by=None,
    )
    db.session.commit()


# --- The card registry ---------------------------------------------------------------------------


@pytest.fixture
def extra_cards():
    before = list(widgets._home_cards)
    yield
    widgets._home_cards[:] = before


def test_cards_are_filtered_and_empty_ones_left_out(app, client, login, extra_cards):
    widgets.home_card('home/_numbers.html', permission='system.admin', area=widgets.CLUB)(lambda m: {'rows': []})
    widgets.home_card('home/_numbers.html', area=widgets.CLUB)(lambda m: None)
    user = make_user()
    with app.test_request_context():
        from flask_login import login_user

        login_user(user)
        cards = widgets.home_cards(user.member)
    assert all(data != {'rows': []} for _, data in cards[widgets.CLUB])
    with pytest.raises(ValueError):
        widgets.home_card('x.html', area='sidebar')


def test_a_broken_card_does_not_break_the_page(app, client, login, extra_cards):
    def broken(member):
        raise RuntimeError('boom')

    widgets.home_card('home/_numbers.html', order=1)(broken)
    login(make_user())
    with pytest.raises(RuntimeError):  # tests see the error ...
        client.get('/')
    app.testing = False  # ... in production the card is left out and logged
    assert 'Hallo Max!' in page(client)


# --- What a member sees --------------------------------------------------------------------------


def test_header_shows_rank_office_and_semester_week(client, login):
    today = today_local()
    create_semester('XS', 'WS', today - timedelta(days=15), today + timedelta(days=100))
    db.session.commit()
    officer = make_officer('kassenwart', name='Kurt Kasse')
    login(officer)
    text = page(client)
    assert 'Hallo Kurt!' in text and 'Bursch · Kassenwart' in text and 'XS · Woche 3' in text


def test_member_sees_own_things_and_nothing_of_the_aemter(client, login):
    user = make_user()
    member = user.member
    charge(member, 1250)
    today = today_local()
    db.session.add_all(
        [
            Chore(title='Küche putzen', assigned_to_id=user.id, due_date=today - timedelta(days=2)),
            Chore(title='Flur saugen', assigned_to_id=user.id, due_date=today + timedelta(days=3)),
            Chore(title='Schon erledigt', assigned_to_id=user.id, is_done=True),
            Announcement(title='Kneipe am Freitag', body='Um 20 Uhr.', author_id=user.id),
        ]
    )
    room = Resource(name='Kneipsaal')
    db.session.add(room)
    db.session.commit()
    start = utcnow() + timedelta(days=2)
    create_booking(room.id, user.id, start, start + timedelta(hours=3))
    residents = next(g for g in groups() if g.kind == 'residence')
    add_to_group(residents, member, today - timedelta(days=30))  # laundry is for Hausbewohner
    record_use(member, washes=2, dried=False, submission_id='home-1', recorded_by=user)
    notify(member, 'rank_changed', 'Du bist jetzt Bursch.')
    db.session.commit()

    login(user)
    text = page(client)
    for expected in (
        'Mein Saldo',
        '-12,50 €',
        'Meine Aufgaben',
        'Überfällig seit',
        'Flur saugen',
        'Meine Wäsche',
        'Demnächst im Haus',
        'Kneipsaal',
        'Ankündigungen',
        'Kneipe am Freitag',
        'Neu für dich',
        'Du bist jetzt Bursch.',
        'Der Bund in Zahlen',
    ):
        assert expected in text, expected
    assert 'Schon erledigt' not in text
    for office_card in ('Für deine Ämter', 'Gegenzeichnung', 'offene Einladungen', 'fehlgeschlagene Jobs'):
        assert office_card not in text


def test_cards_without_content_are_left_out(client, login):
    login(make_user())
    text = page(client)
    for card in ('Mein Saldo', 'Meine Aufgaben', 'Meine Wäsche', 'Demnächst im Haus', 'Neu für dich'):
        assert card not in text, card


def test_done_from_the_start_page_returns_there(client, login):
    user = make_user()
    chore = Chore(title='Küche putzen', assigned_to_id=user.id)
    db.session.add(chore)
    db.session.commit()
    login(user)
    response = client.post(f'/chores/{chore.id}/toggle?next=/')
    assert response.status_code == 302 and response.location == '/'
    assert 'Meine Aufgaben' not in page(client)
    response = client.post(f'/chores/{chore.id}/toggle?next=https://evil.example')
    assert response.location == '/chores'


def test_news_and_numbers_of_the_bund(client, login):
    today = today_local()
    newcomer = make_member('Neu Ling')
    set_rank(newcomer, 'fux', today - timedelta(days=3))
    old = make_member('Alt Bekannt')
    set_rank(old, 'fux', date(2020, 1, 1))
    set_rank(old, 'bursch', today - timedelta(days=1))
    make_member('Ohne Rang')
    db.session.commit()
    login(make_user())
    text = page(client)
    assert 'Neu im Bund: Neu Ling (Fux)' in text and 'Alt Bekannt ist jetzt Bursch' in text
    assert 'Mitglieder' in text


# --- Cards for the Ämter -------------------------------------------------------------------------


def test_kassenwart_and_kassenpruefer_see_the_kasse(app, client, login):
    debtor = make_member('Max Muster')
    charge(debtor, 2000)
    charges.save_recurring(
        None,
        title='Aktivenbeitrag',
        amount_cents=750,
        target=account_by_key('beitraege'),
        category=None,
        rank_keys=['bursch'],
        group=None,
        actor=None,
    )
    db.session.commit()
    login(make_officer('kassenwart'))
    text = page(client)
    assert 'Für deine Ämter' in text and '-20,00 €' in text and '1 Mitglied schuldet' in text
    assert 'Noch nicht gebucht für' in text and 'Aktivenbeitrag' in text

    pruefer = make_officer('kassenpruefer')
    other = app.test_client()
    other.post('/login', data={'email': pruefer.login_email, 'password': 'correct horse battery'})
    text = page(other)
    assert 'Gegenzeichnung' in text and 'Nichts wartet auf dich.' in text


def test_senior_sees_open_invitations(client, login):
    from .factories import make_invitation

    invitation, _ = make_invitation(make_member('Neu Ling'))
    login(make_officer('senior'))
    text = page(client)
    assert 'Neue Mitglieder' in text and 'offene Einladungen' in text and 'Neu Ling' in text
    assert f'/members/invited/{invitation.id}' in text
