"""M6 (first part): products and prices, the Bierwart's Strichliste, corrections, my Deckel, Bierkönig."""

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from community.core.identity.models import MemberStatus
from community.core.time import today_local, utcnow
from community.extensions import db
from community.modules.drinks import services as drinks
from community.modules.drinks.models import DrinkConsumption

from .factories import make_member, make_officer, make_user

DAY = date(2026, 10, 1)


def rows():
    return db.session.scalar(select(func.count()).select_from(DrinkConsumption))


@pytest.fixture
def pils(app):
    product = drinks.create_product('Pils', 120, counts_as_beer=True, volume_ml=500, valid_from=DAY, actor=None)
    db.session.commit()
    return product


@pytest.fixture
def cola(app):
    product = drinks.create_product('Cola', 150, counts_as_beer=False, valid_from=DAY, actor=None)
    db.session.commit()
    return product


# --- Products and prices ---------------------------------------------------------------------------


def test_price_history_and_the_price_copied_into_striche(pils):
    member = make_member('Max Muster')
    first, _ = drinks.record(member, pils, 2, utcnow() - timedelta(days=1), source='sheet')
    drinks.set_price(pils, 150, today_local(), actor=None)
    second, _ = drinks.record(member, pils, 1, utcnow(), source='sheet')
    db.session.commit()
    assert drinks.price_on(pils, DAY) == 120 and drinks.price_on(pils, today_local()) == 150
    assert (first.unit_price_cents, first.total_cents) == (120, 240)
    assert second.unit_price_cents == 150
    assert drinks.price_on(pils, DAY - timedelta(days=1)) is None


def test_product_rules(pils):
    with pytest.raises(drinks.DrinksError, match='gibt es schon'):
        drinks.create_product('pils', 100, counts_as_beer=True, actor=None)
    with pytest.raises(drinks.DrinksError, match='negativ'):
        drinks.set_price(pils, -1, DAY, actor=None)
    drinks.update_product(pils, name='Pils 0,5', counts_as_beer=True, volume_ml=500, archived=True, actor=None)
    db.session.commit()
    assert not pils.is_active and drinks.products() == []
    with pytest.raises(drinks.DrinksError, match='nicht mehr verkauft'):
        drinks.record(make_member('Max Muster'), pils, 1, utcnow(), source='sheet')


def test_striche_rules(pils):
    member = make_member('Max Muster')
    for quantity in (0, -1, 51, 1.5, True):
        with pytest.raises(drinks.DrinksError, match='Anzahl'):
            drinks.record(member, pils, quantity, utcnow(), source='sheet')
    member.status = MemberStatus.ENDED
    with pytest.raises(drinks.DrinksError, match='kein aktives Mitglied'):
        drinks.record(member, pils, 1, utcnow(), source='sheet')
    member.status = MemberStatus.ACTIVE
    with pytest.raises(drinks.DrinksError, match='noch keinen Preis'):
        drinks.record(member, pils, 1, utcnow() - timedelta(days=400), source='sheet')


def test_a_correction_cancels_once(pils):
    member = make_member('Max Muster')
    row, _ = drinks.record(member, pils, 3, utcnow(), source='sheet')
    with pytest.raises(drinks.DrinksError, match='Grund'):
        drinks.correct(row, corrected_by=None, reason=' ')
    fix = drinks.correct(row, corrected_by=None, reason='falsche Person')
    db.session.commit()
    assert (fix.quantity, fix.corrects_id, fix.total_cents) == (-3, row.id, -360)
    with pytest.raises(drinks.DrinksError, match='schon korrigiert'):
        drinks.correct(row, corrected_by=None, reason='nochmal')
    with pytest.raises(drinks.DrinksError, match='Korrektur kann nicht'):
        drinks.correct(fix, corrected_by=None, reason='nochmal')
    month = drinks.month_range()
    assert drinks.deckel(member, *month) == drinks.Deckel(0, 0, 0)


# --- Numbers -----------------------------------------------------------------------------------------


def test_deckel_ranking_and_barometer_count_only_beer(pils, cola):
    max_, eva, gone = make_member('Max Muster'), make_member('Eva Erste'), make_member('Ex Mitglied')
    now = utcnow()
    drinks.record(max_, pils, 3, now, source='sheet')
    drinks.record(max_, cola, 2, now, source='sheet')
    drinks.record(eva, pils, 5, now, source='sheet')
    drinks.record(gone, pils, 9, now, source='sheet')
    gone.status = MemberStatus.ENDED
    db.session.commit()
    month = drinks.month_range()
    assert drinks.deckel(max_, *month) == drinks.Deckel(beers=3, drinks=5, total_cents=660)
    assert [(m.full_name, beers) for m, beers in drinks.beer_ranking(*month)] == [('Eva Erste', 5), ('Max Muster', 3)]
    assert drinks.beer_totals(*month) == (17, 8.5)
    assert drinks.next_milestone(17) == 100 and drinks.next_milestone(100) == 200


# --- Pages -------------------------------------------------------------------------------------------


def sheet_token(client):
    page = client.get('/getraenke/strichliste').get_data(as_text=True)
    return page.split('name="token" type="hidden" value="')[1].split('"')[0]


def test_bierwart_enters_a_strichliste_once(client, login, pils, cola):
    max_, eva = make_member('Max Muster'), make_member('Eva Erste')
    login(make_officer('bierwart'))
    page = client.get('/getraenke/strichliste').get_data(as_text=True)
    assert f'name="q-{max_.id}-{pils.id}"' in page and 'Cola' in page
    data = {
        'day': today_local().isoformat(),
        'token': sheet_token(client),
        f'q-{max_.id}-{pils.id}': '4',
        f'q-{eva.id}-{cola.id}': '1',
        f'q-{eva.id}-{pils.id}': '',
    }
    response = client.post('/getraenke/strichliste', data=data)
    assert response.status_code == 302 and rows() == 2
    client.post('/getraenke/strichliste', data=data)  # sent twice: nothing new
    assert rows() == 2
    assert drinks.deckel(max_, *drinks.month_range()).beers == 4

    bad = {**data, 'token': 'other', f'q-{max_.id}-{pils.id}': 'vier'}
    page = client.post('/getraenke/strichliste', data=bad).get_data(as_text=True)
    assert 'ist keine Anzahl' in page and rows() == 2
    future = {**data, 'token': 'later', 'day': (today_local() + timedelta(days=1)).isoformat()}
    assert 'Zukunft' in client.post('/getraenke/strichliste', data=future).get_data(as_text=True)
    assert rows() == 2


def test_bierwart_corrects_from_the_list(client, login, pils):
    member = make_member('Max Muster')
    row, _ = drinks.record(member, pils, 2, utcnow(), source='sheet')
    db.session.commit()
    login(make_officer('bierwart'))
    assert 'Korrigieren' in client.get('/getraenke/liste').get_data(as_text=True)
    client.post(f'/getraenke/{row.id}/korrigieren', data={'reason': 'doppelt'})
    page = client.get('/getraenke/liste').get_data(as_text=True)
    assert 'Korrektur: doppelt' in page and 'korrigiert' in page and rows() == 2


def test_bierwart_manages_products_and_prices(client, login):
    login(make_officer('bierwart'))
    client.post(
        '/getraenke/verwaltung', data={'name': 'Helles', 'price': '1,30', 'counts_as_beer': 'y', 'volume_ml': '500'}
    )
    product = drinks.products()[0]
    assert (product.name, drinks.price_on(product, today_local())) == ('Helles', 130)
    client.post(
        f'/getraenke/verwaltung/{product.id}',
        data={
            'preis-price': '1,40',
            'preis-valid_from': (today_local() + timedelta(days=7)).isoformat(),
            'preis-submit': 'y',
        },
    )
    assert drinks.price_on(product, today_local()) == 130
    assert drinks.price_on(product, today_local() + timedelta(days=7)) == 140
    page = client.get(f'/getraenke/verwaltung/{product.id}').get_data(as_text=True)
    assert '1,40 €' in page and '1,30 €' in page


def test_member_sees_own_deckel_and_only_beer_counts_of_others(client, login, pils, cola):
    user = make_user()
    other = make_member('Eva Erste')
    drinks.record(user.member, cola, 2, utcnow(), source='sheet')
    drinks.record(other, pils, 6, utcnow(), source='sheet')
    db.session.commit()
    login(user)
    page = client.get('/getraenke/').get_data(as_text=True)
    assert 'Mein Deckel' in page and '3,00 €' in page and 'Eva Erste' in page
    assert '7,20 €' not in page  # Eva's amount stays private
    assert client.get('/getraenke/liste').status_code == 403
    assert client.get('/getraenke/strichliste').status_code == 403


def test_start_page_cards(client, login, pils):
    bierwart = make_officer('bierwart')
    drinks.record(bierwart.member, pils, 12, utcnow(), source='sheet')
    drinks.record(make_member('Eva Erste'), pils, 3, utcnow(), source='sheet')
    db.session.commit()
    login(bierwart)
    page = client.get('/').get_data(as_text=True)
    assert 'Mein Deckel' in page and 'Bierkönig im' in page and 'Fass-Barometer' in page
    assert 'Noch 85 bis zum 100. Bier' in page and '7,5 Liter' in page
    assert 'Strichliste eintragen' in page


def test_no_drink_cards_without_striche(client, login):
    login(make_user())
    page = client.get('/').get_data(as_text=True)
    assert 'Mein Deckel' not in page and 'Bierkönig' not in page and 'Fass-Barometer' not in page
