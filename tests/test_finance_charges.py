"""M3-4: Umlage (many members at once, with a Kasse paying part for some ranks) and monthly charges."""

from datetime import date

import pytest
from sqlalchemy import func, select

from community.core.identity.models import MemberStatus
from community.core.org.services import add_to_group, create_group, rank_by_key
from community.extensions import db
from community.modules.finance import charges
from community.modules.finance.models import FinCategory, FinJournal, FinRecurring, JournalKind
from community.modules.finance.services import FinanceError, account_balance, account_by_key, saldo

from .factories import make_member, make_officer, set_rank

DAY = date(2026, 10, 10)


def journal_count():
    return db.session.scalar(select(func.count()).select_from(FinJournal))


def member(name, rank):
    person = make_member(name)
    set_rank(person, rank)
    return person


def kneipen():
    return db.session.scalar(select(FinCategory).where(FinCategory.name == 'Kneipen'))


# --- Splitting ------------------------------------------------------------------------------


def test_equal_shares_are_exact_to_the_cent():
    assert charges.equal_shares(1000, [3, 1, 2]) == {1: 334, 2: 333, 3: 333}
    assert sum(charges.equal_shares(10001, list(range(1, 8))).values()) == 10001
    with pytest.raises(FinanceError):
        charges.equal_shares(1000, [])


def test_the_fuxenkasse_pays_part_of_a_fux_share(app):
    fux, bursch = member('Fritz Fux', 'fux'), member('Bernd Bursch', 'bursch')
    subsidy = charges.Subsidy(account_by_key('fuxenkasse'), frozenset({rank_by_key('fux').id}), 3000)
    shares = charges.plan([bursch, fux], DAY, per_person_cents=7000, subsidy=subsidy)
    by_name = {s.member.first_name: s for s in shares}
    assert (by_name['Fritz'].subsidy_cents, by_name['Fritz'].due_cents) == (3000, 4000)
    assert (by_name['Bernd'].subsidy_cents, by_name['Bernd'].due_cents) == (0, 7000)
    assert charges.totals(shares) == {'share': 14000, 'subsidy': 3000, 'due': 11000, 'people': 2}

    journal, _ = charges.post(
        shares,
        title='Stiftungsfest',
        booked_on=DAY,
        target=account_by_key('verschiedenes'),
        category=kneipen(),
        posted_by=None,
        subsidy=subsidy,
    )
    db.session.commit()
    assert journal.kind == JournalKind.SPLIT
    assert saldo(fux) == -4000 and saldo(bursch) == -7000
    assert account_balance(account_by_key('fuxenkasse')) == 3000
    assert account_balance(account_by_key('verschiedenes')) == -14000
    assert any(entry.memo == 'Anteil Fritz Fux' for entry in journal.entries)


def test_a_subsidy_never_exceeds_the_share(app):
    fux = member('Fritz Fux', 'fux')
    subsidy = charges.Subsidy(account_by_key('fuxenkasse'), frozenset({rank_by_key('fux').id}), 5000)
    (share,) = charges.plan([fux], DAY, per_person_cents=2000, subsidy=subsidy)
    assert (share.subsidy_cents, share.due_cents) == (2000, 0)


def test_audience_combines_ranks_group_and_picks_once(app):
    fux = member('Fritz Fux', 'fux')
    member('Bernd Bursch', 'bursch')
    resident, former = make_member('Hanna Haus'), member('Ex Mitglied', 'bursch')
    former.status = MemberStatus.ENDED
    group = create_group('Hausbewohner Test')
    add_to_group(group, resident, date(2026, 1, 1))
    add_to_group(group, fux, date(2026, 1, 1))
    db.session.commit()
    people = charges.audience(DAY, rank_ids=[rank_by_key('bursch').id], group=group, member_ids=[fux.id])
    assert [p.full_name for p in people] == ['Bernd Bursch', 'Fritz Fux', 'Hanna Haus']
    with pytest.raises(FinanceError, match='mindestens eine'):
        charges.plan([], DAY, per_person_cents=100)


# --- Umlage page --------------------------------------------------------------------------------


def umlage_form(**overrides):
    data = {
        'title': 'Stiftungsfest Dresden',
        'booked_on': '2026-10-10',
        'beleg': 'B5',
        'target': str(account_by_key('verschiedenes').id),
        'category': str(kneipen().id),
        'mode': 'person',
        'amount': '70,00',
        'rank_ids': [str(rank_by_key('fux').id), str(rank_by_key('bursch').id)],
        'group_id': '0',
        'subsidy_account': str(account_by_key('fuxenkasse').id),
        'subsidy_rank_ids': [str(rank_by_key('fux').id)],
        'subsidy_amount': '30',
    }
    data.update(overrides)
    return data


def test_umlage_is_previewed_then_booked_once(client, login):
    fux, bursch = member('Fritz Fux', 'fux'), member('Bernd Bursch', 'bursch')
    kassenwart = make_officer('kassenwart')
    login(kassenwart)
    assert client.get('/kasse/umlage').status_code == 200

    page = client.post('/kasse/umlage', data={**umlage_form(), 'preview': 'y'}).get_data(as_text=True)
    assert 'Vorschau' in page and 'Noch nicht gebucht' in page and '40,00 €' in page
    assert journal_count() == 0
    token = page.split('name="token" type="hidden" value="')[1].split('"')[0]

    confirm = {**umlage_form(), 'token': token, 'confirm': 'y'}
    response = client.post('/kasse/umlage', data=confirm)
    assert response.status_code == 302 and '/kasse/buchung/' in response.location
    client.post('/kasse/umlage', data=confirm)  # a second click books nothing new
    assert journal_count() == 1
    # The Kassenwart (a Bursch) is in the audience too.
    assert saldo(fux) == -4000 and saldo(bursch) == -7000 and saldo(kassenwart.member) == -7000


def test_umlage_split_of_a_total_and_errors(client, login):
    member('Fritz Fux', 'fux')
    member('Bernd Bursch', 'bursch')
    login(make_officer('kassenwart'))
    total = umlage_form(mode='total', amount='100', subsidy_account='0', subsidy_amount='', subsidy_rank_ids=[])
    page = client.post('/kasse/umlage', data={**total, 'preview': 'y'}).get_data(as_text=True)
    assert '33,34 €' in page and '33,33 €' in page and '100,00 €' in page

    page = client.post('/kasse/umlage', data={**umlage_form(subsidy_rank_ids=[]), 'preview': 'y'})
    assert 'Rang und Betrag' in page.get_data(as_text=True)
    page = client.post('/kasse/umlage', data={**umlage_form(rank_ids=[]), 'preview': 'y'})
    assert 'mindestens eine Person' in page.get_data(as_text=True)
    assert journal_count() == 0


def test_kassenpruefer_cannot_charge(client, login):
    login(make_officer('kassenpruefer'))
    assert client.get('/kasse/umlage').status_code == 403
    assert client.get('/kasse/monatlich').status_code == 403


# --- Monthly charges ---------------------------------------------------------------------------


def test_monthly_charge_is_booked_once_per_month(client, login):
    fux, bursch = member('Fritz Fux', 'fux'), member('Bernd Bursch', 'bursch')
    login(make_officer('kassenwart'))
    form = {
        'title': 'Aktivenbeitrag',
        'amount': '7,50',
        'target': str(account_by_key('beitraege').id),
        'category': '',
        'rank_keys': ['fux', 'bursch'],
        'group_id': '0',
    }
    response = client.post('/kasse/monatlich', data=form)
    assert response.status_code == 302
    item = db.session.scalar(select(FinRecurring))
    page = client.get(f'/kasse/monatlich/{item.id}?monat=2026-11').get_data(as_text=True)
    assert 'November 2026' in page and 'Fritz Fux' in page and '22,50 €' in page  # with the Kassenwart

    client.post(f'/kasse/monatlich/{item.id}?monat=2026-11')
    client.post(f'/kasse/monatlich/{item.id}?monat=2026-11')
    assert journal_count() == 1
    journal = db.session.scalar(select(FinJournal))
    assert journal.title == 'Aktivenbeitrag November 2026' and journal.booked_on == date(2026, 11, 1)
    assert saldo(fux) == -750 and saldo(bursch) == -750
    assert 'gebucht' in client.get('/kasse/monatlich?monat=2026-11').get_data(as_text=True)
    assert 'ist gebucht' in client.get(f'/kasse/monatlich/{item.id}?monat=2026-11').get_data(as_text=True)

    client.post(f'/kasse/monatlich/{item.id}/bearbeiten', data={**form, 'amount': '8,00'})
    db.session.expire_all()
    assert item.amount_cents == 800


def test_monthly_charge_rules(app):
    with pytest.raises(FinanceError, match='Zielgruppe'):
        charges.save_recurring(
            None,
            title='X',
            amount_cents=100,
            target=account_by_key('beitraege'),
            category=None,
            rank_keys=[],
            group=None,
            actor=None,
        )
    with pytest.raises(FinanceError, match='Kostenstelle'):
        charges.save_recurring(
            None,
            title='X',
            amount_cents=100,
            target=account_by_key('verschiedenes'),
            category=None,
            rank_keys=['fux'],
            group=None,
            actor=None,
        )
    item = charges.save_recurring(
        None,
        title='Internet',
        amount_cents=400,
        target=account_by_key('telefon'),
        category=None,
        rank_keys=['alter_herr'],
        group=None,
        actor=None,
    )
    with pytest.raises(FinanceError, match='Niemand'):
        charges.post_recurring(item, date(2026, 11, 1), posted_by=None)
