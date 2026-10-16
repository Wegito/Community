"""M3-1: the ledger core. Bookings balance, are never edited, and Salden are computed."""

from datetime import date

import pytest
from sqlalchemy import select

from community.core.authz.service import permissions_for
from community.core.org.models import OfficeIncompatibility
from community.core.org.services import create_semester, office_by_key
from community.core.platform.audit import AuditLog
from community.core.time import utcnow
from community.extensions import db
from community.modules.finance.models import AccountKind, FinCategory, FinJournal, JournalKind
from community.modules.finance.services import (
    CosignRequired,
    FinanceError,
    Line,
    account_balance,
    account_by_key,
    accounts,
    categories,
    default_book,
    member_account,
    member_saldi,
    next_beleg,
    post_journal,
    reverse,
    saldo,
    statement,
)

from .factories import make_admin, make_member, make_officer

DAY = date(2026, 10, 5)


def cost_centre(name='Kneipen'):
    return db.session.scalar(select(FinCategory).where(FinCategory.name == name))


def charge(member, cents, *, title='Kneipe', posted_by=None, **kwargs):
    card = member_account(member)
    journal, _ = post_journal(
        JournalKind.CHARGE,
        title,
        DAY,
        [Line(card, account_by_key('verschiedenes'), cents, cost_centre())],
        posted_by=posted_by,
        **kwargs,
    )
    return journal


def pay(member, cents, *, posted_by=None, **kwargs):
    journal, _ = post_journal(
        JournalKind.PAYMENT,
        'Überweisung',
        DAY,
        [Line(account_by_key('giro'), member_account(member), cents)],
        posted_by=posted_by,
        **kwargs,
    )
    return journal


# --- Seed data -------------------------------------------------------------------------


def test_the_excel_accounts_and_cost_centres_are_seeded(app):
    book = default_book()
    assert book.label == 'Aktivenkasse'
    keys = {account.key for account in accounts()}
    assert {'giro', 'bar', 'spar', 'kueche', 'verschiedenes', 'burschenkasse', 'fuxenkasse', 'heimverein'} <= keys
    assert account_by_key('verschiedenes').needs_cost_centre
    assert not account_by_key('giro').needs_cost_centre
    assert 'Stiftungsfest' in [category.name for category in categories()]


def test_kassenpruefer_office_reads_and_cosigns_but_does_not_book(app):
    office = office_by_key('kassenpruefer')
    assert office.max_holders == 2
    assert {rank.key for rank in office.eligible_ranks} == {'bursch', 'alter_herr'}
    pair = sorted((office.id, office_by_key('kassenwart').id))
    assert db.session.get(OfficeIncompatibility, tuple(pair)) is not None

    pruefer = permissions_for(make_officer('kassenpruefer').member)
    assert {'finance.ledger.view_all', 'finance.reports.view', 'finance.journal.cosign'} <= pruefer
    assert 'finance.journal.post' not in pruefer
    kassenwart = permissions_for(make_officer('kassenwart').member)
    assert {'finance.journal.post', 'finance.journal.reverse', 'finance.accounts.manage'} <= kassenwart


def test_only_kassenwart_and_kassenpruefer_see_the_kasse(app):
    """Q22: the Senior and the technical admin do not; every member sees their own Saldo."""
    assert 'finance.ledger.view_all' not in permissions_for(make_officer('senior').member)
    admin = permissions_for(make_admin().member)
    assert not {code for code in admin if code.startswith('finance.')} - {'finance.saldo.view_own'}
    assert 'finance.saldo.view_own' in permissions_for(make_officer('fuxmajor').member)


# --- Posting -----------------------------------------------------------------------------


def test_charge_and_payment_move_the_saldo(app):
    member = make_member('Max Muster')
    assert saldo(member) == 0
    charge(member, 1250)
    assert saldo(member) == -1250
    pay(member, 2000)
    db.session.commit()
    assert saldo(member) == 750
    assert account_balance(account_by_key('giro')) == 2000
    assert [(row[0].member_id, row[1]) for row in member_saldi()] == [(member.id, 750)]


def test_a_card_is_created_once_per_member(app):
    member = make_member('Max Muster')
    assert member_account(member, create=False) is None
    card = member_account(member)
    assert card.kind == AccountKind.MEMBER and card.name == 'Max Muster'
    assert member_account(member).id == card.id


@pytest.mark.parametrize('amount', [0, -5, 12.5, True, '100'])
def test_amounts_must_be_positive_whole_cents(app, amount):
    member = make_member('Max Muster')
    with pytest.raises(FinanceError, match='größer als 0'):
        post_journal(
            JournalKind.PAYMENT,
            'x',
            DAY,
            [Line(account_by_key('giro'), member_account(member), amount)],
            posted_by=None,
        )


def test_invalid_bookings_are_refused(app):
    giro, bar = account_by_key('giro'), account_by_key('bar')
    with pytest.raises(FinanceError, match='verschieden'):
        post_journal(JournalKind.MANUAL, 'x', DAY, [Line(giro, giro, 100)], posted_by=None)
    with pytest.raises(FinanceError, match='mindestens einen'):
        post_journal(JournalKind.MANUAL, 'x', DAY, [], posted_by=None)
    with pytest.raises(FinanceError, match='Buchungstext'):
        post_journal(JournalKind.MANUAL, '  ', DAY, [Line(giro, bar, 100)], posted_by=None)
    with pytest.raises(FinanceError, match='Kostenstelle'):
        post_journal(JournalKind.MANUAL, 'x', DAY, [Line(account_by_key('verschiedenes'), giro, 100)], posted_by=None)
    bar.archived_at = utcnow()
    with pytest.raises(FinanceError, match='archiviert'):
        post_journal(JournalKind.MANUAL, 'x', DAY, [Line(giro, bar, 100)], posted_by=None)


def test_a_booking_is_audited(app):
    kassenwart = make_officer('kassenwart')
    journal = pay(make_member('Max Muster'), 500, posted_by=kassenwart, beleg='G1')
    entry = db.session.scalar(select(AuditLog).where(AuditLog.action == 'finance.journal.post'))
    assert entry.entity_id == str(journal.id) and entry.actor_user_id == kassenwart.id
    assert '"total_cents": 500' in entry.diff_json


def test_the_same_idempotency_key_books_once(app):
    member = make_member('Max Muster')
    first = pay(member, 500, idempotency_key='bank:2026-10:42')
    again, created = post_journal(
        JournalKind.PAYMENT,
        'Überweisung',
        DAY,
        [Line(account_by_key('giro'), member_account(member), 500)],
        posted_by=None,
        idempotency_key='bank:2026-10:42',
    )
    assert again.id == first.id and created is False
    assert saldo(member) == 500


# --- Never edited, only reversed ----------------------------------------------------


def test_bookings_cannot_be_changed_or_deleted(app):
    journal = pay(make_member('Max Muster'), 500)
    db.session.commit()
    journal.title = 'geändert'
    with pytest.raises(FinanceError, match='nie geändert'):
        db.session.flush()
    db.session.rollback()
    db.session.delete(journal.entries[0])
    with pytest.raises(FinanceError, match='nie gelöscht'):
        db.session.flush()
    db.session.rollback()


def test_reversal_moves_everything_back_once(app):
    kassenwart = make_officer('kassenwart')
    member = make_member('Max Muster')
    journal = charge(member, 1250, posted_by=kassenwart, beleg='B3')
    db.session.commit()
    with pytest.raises(FinanceError, match='Grund'):
        reverse(journal, reversed_by=kassenwart, reason=' ')
    reversal = reverse(journal, reversed_by=kassenwart, reason='doppelt erfasst')
    db.session.commit()
    assert saldo(member) == 0
    assert reversal.kind == JournalKind.REVERSAL and reversal.reverses_journal_id == journal.id
    assert reversal.title == 'Storno: Kneipe' and reversal.beleg == 'B3' and reversal.note == 'doppelt erfasst'
    assert reversal.entries[0].category.name == 'Kneipen'
    assert db.session.get(FinJournal, journal.id).reversed_by.id == reversal.id
    with pytest.raises(FinanceError, match='bereits storniert'):
        reverse(journal, reversed_by=kassenwart, reason='nochmal')
    with pytest.raises(FinanceError, match='kann nicht storniert'):
        reverse(reversal, reversed_by=kassenwart, reason='zurück')


# --- Four eyes: nobody credits their own Kontokarte alone ---------------------------------


def test_crediting_your_own_card_needs_a_second_person(app):
    kassenwart = make_officer('kassenwart')
    own_card = member_account(kassenwart.member)
    auslage = [Line(account_by_key('verschiedenes'), own_card, 3000, cost_centre(), 'Einkauf Kneipe')]
    with pytest.raises(CosignRequired, match='Gegenzeichnung'):
        post_journal(JournalKind.EXPENSE, 'Auslage', DAY, auslage, posted_by=kassenwart)
    with pytest.raises(CosignRequired, match='andere Person'):
        post_journal(JournalKind.EXPENSE, 'Auslage', DAY, auslage, posted_by=kassenwart, cosigned_by=kassenwart)
    plain = make_officer('fuxmajor')
    with pytest.raises(CosignRequired, match='darf keine'):
        post_journal(JournalKind.EXPENSE, 'Auslage', DAY, auslage, posted_by=kassenwart, cosigned_by=plain)

    pruefer = make_officer('kassenpruefer')
    journal, _ = post_journal(JournalKind.EXPENSE, 'Auslage', DAY, auslage, posted_by=kassenwart, cosigned_by=pruefer)
    assert journal.cosigned_by_user_id == pruefer.id
    assert saldo(kassenwart.member) == 3000
    # Charging yourself or booking for others needs nobody else.
    charge(kassenwart.member, 500, posted_by=kassenwart)
    pay(make_member('Max Muster'), 500, posted_by=kassenwart)


def test_reversing_a_charge_on_your_own_card_needs_a_second_person(app):
    kassenwart = make_officer('kassenwart')
    journal = charge(kassenwart.member, 500, posted_by=kassenwart)
    with pytest.raises(CosignRequired):
        reverse(journal, reversed_by=kassenwart, reason='falsch')
    reverse(journal, reversed_by=kassenwart, reason='falsch', cosigned_by=make_officer('kassenpruefer'))
    assert saldo(kassenwart.member) == 0


# --- Statements and receipt numbers -------------------------------------------------------


def test_statement_with_running_balance_and_opening(app):
    member = make_member('Max Muster')
    charge(member, 1000)
    post_journal(
        JournalKind.CHARGE,
        'Stiftungsfest',
        date(2026, 10, 20),
        [Line(member_account(member), account_by_key('verschiedenes'), 2500, cost_centre('Stiftungsfest'))],
        posted_by=None,
    )
    post_journal(
        JournalKind.PAYMENT,
        'Überweisung',
        date(2026, 10, 25),
        [Line(account_by_key('giro'), member_account(member), 3000)],
        posted_by=None,
    )
    opening, rows, closing = statement(member_account(member))
    assert opening == 0 and closing == -500
    assert [(row.amount_cents, row.balance_cents) for row in rows] == [(-1000, -1000), (-2500, -3500), (3000, -500)]
    assert rows[2].other.key == 'giro'

    opening, rows, closing = statement(member_account(member), start=date(2026, 10, 15))
    assert opening == -1000 and len(rows) == 2 and closing == -500
    opening, rows, closing = statement(account_by_key('giro'))
    assert (opening, closing, rows[0].amount_cents) == (0, 3000, 3000)


def test_receipt_numbers_count_per_series_and_semester(app):
    create_semester('WS26/27', 'WS', date(2026, 10, 1), date(2027, 3, 31))
    member = make_member('Max Muster')
    assert next_beleg('g', DAY) == 'G1'
    pay(member, 100, beleg='G1')
    pay(member, 100, beleg='G2')
    charge(member, 100, beleg='B1')
    db.session.commit()
    assert next_beleg('G', DAY) == 'G3'
    assert next_beleg('B', DAY) == 'B2'
    assert next_beleg('K', DAY) == 'K1'
    assert db.session.scalar(select(FinJournal).where(FinJournal.beleg == 'G2')).semester.code == 'WS26/27'
    # Next semester starts again at 1.
    create_semester('SS27', 'SS', date(2027, 4, 1), date(2027, 9, 30))
    assert next_beleg('G', date(2027, 4, 10)) == 'G1'


def test_a_semester_with_bookings_is_kept(app):
    from community.core.org.services import OrgError, delete_semester

    semester = create_semester('WS26/27', 'WS', date(2026, 10, 1), date(2027, 3, 31))
    pay(make_member('Max Muster'), 100)
    db.session.commit()
    with pytest.raises(OrgError, match='Buchungen'):
        delete_semester(semester)
