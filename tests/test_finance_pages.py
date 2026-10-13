"""M3-2 and M3-3: the member's own Saldo, and the Kassenwart's pages."""

from datetime import date

from sqlalchemy import func, select

from community.core.club import update_club
from community.extensions import db
from community.modules.finance.models import FinAccount, FinCategory, FinJournal, JournalKind
from community.modules.finance.services import (
    Line,
    account_balance,
    account_by_key,
    member_account,
    post_journal,
    saldo,
)

from .factories import make_admin, make_member, make_officer, make_user

DAY = date(2026, 10, 5)


def charge(member, cents, title='Kneipe'):
    category = db.session.scalar(select(FinCategory).where(FinCategory.name == 'Kneipen'))
    journal, _ = post_journal(
        JournalKind.CHARGE,
        title,
        DAY,
        [Line(member_account(member), account_by_key('verschiedenes'), cents, category)],
        posted_by=None,
    )
    db.session.commit()
    return journal


def journal_count():
    return db.session.scalar(select(func.count()).select_from(FinJournal))


def value(account):
    return f'a{account.id}'


# --- The member's own Saldo ------------------------------------------------------------------


def test_profile_shows_my_saldo_and_how_to_pay(client, login):
    user = make_user()
    charge(user.member, 1250)
    other = make_member('Anderer Bursch')
    charge(other, 9900, title='Fremde Buchung')
    login(user)
    page = client.get('/profile').get_data(as_text=True)
    assert 'Mein Saldo' in page and '-12,50 €' in page and 'offen' in page
    assert user.member.payment_ref in page
    mine = client.get('/kasse/mein-konto').get_data(as_text=True)
    assert 'Kneipe' in mine and '-12,50 €' in mine
    assert 'Fremde Buchung' not in mine and '99,00' not in mine


def test_no_saldo_card_without_the_right(client, login):
    login(make_user(perms=[]))
    assert 'Mein Saldo' not in client.get('/profile').get_data(as_text=True)


def test_saldo_card_without_bookings(client, login):
    login(make_user())
    page = client.get('/profile').get_data(as_text=True)
    assert 'Mein Saldo' in page and 'ausgeglichen' in page


# --- Who sees the Kasse (Q22) ------------------------------------------------------------------


def test_only_kassenwart_and_kassenpruefer_open_the_kasse(client, login):
    charge(make_member('Max Muster'), 500)
    for office in ('senior', 'consenior', 'fuxmajor'):
        login(make_officer(office))
        assert client.get('/kasse/').status_code == 403
        client.post('/logout')
    login(make_admin())
    assert client.get('/kasse/').status_code == 403
    client.post('/logout')

    login(make_officer('kassenpruefer'))
    page = client.get('/kasse/').get_data(as_text=True)
    assert 'Max Muster' in page and '-5,00 €' in page
    assert 'Neue Buchung' not in page
    assert client.get('/kasse/kassenbuch').status_code == 200
    assert client.get('/kasse/buchung/neu').status_code == 403
    assert client.post('/kasse/konten', data={'kst-name': 'X', 'kst-submit': 'y'}).status_code == 403


# --- Booking form ------------------------------------------------------------------------------


def test_kassenwart_records_a_payment_from_the_preset(client, login):
    member = make_member('Max Muster')
    charge(member, 2000)
    login(make_officer('kassenwart'))
    form = client.get(f'/kasse/buchung/neu?vorlage=zahlung&mitglied={member.public_id}').get_data(as_text=True)
    assert f'selected value="m{member.id}"' in form
    assert f'selected value="{value(account_by_key("giro"))}"' in form
    assert 'value="G1"' in form

    data = {
        'booked_on': '2026-10-06',
        'beleg': 'G1',
        'title': 'Zahlung',
        'amount': '20,00',
        'debit': value(account_by_key('giro')),
        'credit': f'm{member.id}',
        'category': '',
    }
    response = client.post('/kasse/buchung/neu', data=data)
    assert response.status_code == 302 and '/kasse/buchung/' in response.location
    assert saldo(member) == 0
    journal = db.session.scalar(select(FinJournal).where(FinJournal.beleg == 'G1'))
    assert journal.kind == JournalKind.PAYMENT
    assert 'Zahlung' in client.get(response.location).get_data(as_text=True)

    data.update(beleg='G2', amount='5', weiter='1')
    response = client.post('/kasse/buchung/neu', data=data)
    assert response.location.endswith('/kasse/buchung/neu')
    assert saldo(member) == 500


def test_invalid_bookings_are_refused_and_nothing_is_saved(client, login):
    member = make_member('Max Muster')
    login(make_officer('kassenwart'))
    base = {
        'booked_on': '2026-10-06',
        'title': 'Kneipe',
        'amount': '12,50',
        'debit': f'm{member.id}',
        'credit': value(account_by_key('verschiedenes')),
        'category': '',
    }
    page = client.post('/kasse/buchung/neu', data={**base, 'amount': 'zwölf'}).get_data(as_text=True)
    assert 'Bitte einen Betrag wie 12,50 eingeben.' in page
    page = client.post('/kasse/buchung/neu', data={**base, 'amount': '0'}).get_data(as_text=True)
    assert 'größer als 0' in page
    page = client.post('/kasse/buchung/neu', data={**base, 'credit': base['debit']}).get_data(as_text=True)
    assert 'verschieden' in page
    page = client.post('/kasse/buchung/neu', data=base).get_data(as_text=True)
    assert 'brauchen eine Kostenstelle' in page
    page = client.post('/kasse/buchung/neu', data={**base, 'debit': 'a999999'}).get_data(as_text=True)
    assert 'Keine gültige Auswahl.' in page
    assert journal_count() == 0
    assert member_account(member, create=False) is None or saldo(member) == 0


def test_kassenwart_cannot_credit_their_own_card_alone(client, login):
    kassenwart = make_officer('kassenwart')
    login(kassenwart)
    category = db.session.scalar(select(FinCategory).where(FinCategory.name == 'Kneipen'))
    page = client.post(
        '/kasse/buchung/neu',
        data={
            'booked_on': '2026-10-06',
            'title': 'Auslage Einkauf',
            'amount': '30',
            'debit': value(account_by_key('verschiedenes')),
            'credit': f'm{kassenwart.member_id}',
            'category': str(category.id),
        },
    ).get_data(as_text=True)
    assert 'Gegenzeichnung' in page
    assert journal_count() == 0


def test_reversal_from_the_booking_page(client, login):
    member = make_member('Max Muster')
    journal = charge(member, 1250)
    login(make_officer('kassenwart'))
    page = client.get(f'/kasse/buchung/{journal.id}').get_data(as_text=True)
    assert 'Stornieren' in page
    response = client.post(f'/kasse/buchung/{journal.id}/storno', data={'reason': ''})
    assert response.status_code == 302 and saldo(member) == -1250
    response = client.post(f'/kasse/buchung/{journal.id}/storno', data={'reason': 'falscher Betrag'})
    assert saldo(member) == 0
    page = client.get(response.location).get_data(as_text=True)
    assert 'Storno von' in page and 'falscher Betrag' in page
    page = client.get(f'/kasse/buchung/{journal.id}').get_data(as_text=True)
    assert 'Storniert durch' in page and 'name="reason"' not in page
    journal_list = client.get('/kasse/kassenbuch').get_data(as_text=True)
    assert 'storniert' in journal_list and 'Storno' in journal_list


def test_kontokarte_and_filters(client, login):
    member = make_member('Max Muster')
    charge(member, 1250)
    login(make_officer('kassenpruefer'))
    page = client.get(f'/kasse/kontokarte/{member.public_id}').get_data(as_text=True)
    assert 'Kontokarte Max Muster' in page and '-12,50 €' in page and member.payment_ref in page
    category = db.session.scalar(select(FinCategory).where(FinCategory.name == 'Kneipen'))
    assert 'Kneipe' in client.get(f'/kasse/kassenbuch?kostenstelle={category.id}').get_data(as_text=True)
    other = db.session.scalar(select(FinCategory).where(FinCategory.name == 'Vorträge'))
    assert 'Keine Buchungen.' in client.get(f'/kasse/kassenbuch?kostenstelle={other.id}').get_data(as_text=True)
    page = client.get('/kasse/?nur=schulden').get_data(as_text=True)
    assert 'Max Muster' in page
    assert client.get('/kasse/kontokarte/unbekannt').status_code == 404


# --- Accounts and cost centres --------------------------------------------------------------


def test_kassenwart_manages_accounts_and_cost_centres(client, login):
    login(make_officer('kassenwart'))
    form = {'konto-name': 'Getränkekasse', 'konto-kind': 'fund', 'konto-submit': 'y'}
    assert client.post('/kasse/konten', data=form).status_code == 302
    account = db.session.scalar(select(FinAccount).where(FinAccount.name == 'Getränkekasse'))
    assert account.kind == 'fund'
    page = client.post('/kasse/konten', data=form).get_data(as_text=True)
    assert 'gibt es schon' in page

    response = client.post(f'/kasse/konten/{account.id}', data={'name': 'Bierkasse', 'needs_cost_centre': 'y'})
    assert response.status_code == 302
    db.session.expire_all()
    assert account.name == 'Bierkasse' and account.needs_cost_centre

    assert client.post('/kasse/konten', data={'kst-name': 'Weihnachtsfeier', 'kst-submit': 'y'}).status_code == 302
    category = db.session.scalar(select(FinCategory).where(FinCategory.name == 'Weihnachtsfeier'))
    client.post(f'/kasse/kostenstellen/{category.id}', data={'name': 'Weihnachtskneipe', 'archived': 'y'})
    db.session.expire_all()
    assert category.name == 'Weihnachtskneipe' and category.archived_at is not None


def test_an_account_with_money_cannot_be_archived(client, login):
    post_journal(
        JournalKind.MANUAL,
        'Einzahlung',
        DAY,
        [Line(account_by_key('giro'), account_by_key('bar'), 1000)],
        posted_by=None,
    )
    db.session.commit()
    login(make_officer('kassenwart'))
    bar = account_by_key('bar')
    page = client.post(f'/kasse/konten/{bar.id}', data={'name': 'Barkasse', 'archived': 'y'}).get_data(as_text=True)
    assert 'Bestand' in page
    db.session.expire_all()
    assert bar.archived_at is None and account_balance(bar) == -1000


def test_heimverein_shows_the_clubs_own_name(client, login):
    update_club('Corps Beispiel', heimverein='Hausverein Beispiel', actor=None)
    db.session.commit()
    login(make_officer('kassenwart'))
    assert 'Hausverein Beispiel' in client.get('/kasse/konten').get_data(as_text=True)
    assert 'Hausverein Beispiel' in client.get('/kasse/buchung/neu').get_data(as_text=True)


def test_kasse_is_in_the_menu_only_for_those_who_see_it(client, login):
    login(make_officer('kassenwart'))
    assert 'href="/kasse/"' in client.get('/').get_data(as_text=True)
    client.post('/logout')
    login(make_officer('senior'))
    assert 'href="/kasse/"' not in client.get('/').get_data(as_text=True)


def test_admin_appoints_kassenwart_and_kassenpruefer_but_never_sees_the_kasse(client, login):
    """Owner, 2026-10-13: the admin has full control except the Kasse."""
    from community.core.org.services import office_by_key, office_holders

    from .factories import set_rank

    kassenwart, pruefer = make_member('Kai Kasse'), make_member('Paula Prüfer')
    set_rank(kassenwart, 'bursch')
    set_rank(pruefer, 'alter_herr')
    admin = make_admin()
    login(admin)
    for office, person in (('kassenwart', kassenwart), ('kassenpruefer', pruefer)):
        response = client.post(
            f'/org/offices/{office}/assign',
            data={'member_id': person.id, 'valid_from': '2026-10-01', 'semester_id': 'None', 'end_previous': 'y'},
        )
        assert response.status_code == 302
    assert [a.member_id for a in office_holders(office_by_key('kassenpruefer'), date(2026, 10, 2))] == [pruefer.id]
    assert client.get('/kasse/').status_code == 403
    assert client.get('/kasse/kassenbuch').status_code == 403
    # Not even as Kassenwart: nobody assigns an Amt to themselves.
    page = client.post(
        '/org/offices/kassenwart/assign',
        data={'member_id': admin.member_id, 'valid_from': '2026-10-01', 'semester_id': 'None', 'end_previous': 'y'},
    )
    assert page.status_code == 200 and 'selbst' in page.get_data(as_text=True)
