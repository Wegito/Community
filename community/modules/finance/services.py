"""The ledger: post bookings, reverse them, compute Salden and statements.

Rules (roadmap D6, §6.3):
- Every entry moves a positive amount from one account to another, so a booking always balances.
- Bookings are never edited or deleted (guarded below); a mistake gets a reversal booking.
- A member's Saldo is credits minus debits on their Kontokarte: positive = credit, negative = owes.
- A booking that credits the poster's own Kontokarte needs a cosign by a different person who holds
  finance.journal.cosign (the Kassenwart paying back their own Auslage).
"""

import re
from datetime import date, timedelta
from typing import NamedTuple

from sqlalchemy import event, func, select

from community.core.authz.service import permissions_for
from community.core.org.services import current_semester
from community.core.platform import audit
from community.core.time import today_local
from community.extensions import db

from .models import AccountKind, FinAccount, FinBook, FinCategory, FinEntry, FinJournal, JournalKind

DEFAULT_BOOK = 'aktivenkasse'
TITLE_MAX = 200
COSIGN_PERMISSION = 'finance.journal.cosign'


class FinanceError(Exception):
    """A rule was broken; the message is shown to the user (German)."""


class CosignRequired(FinanceError):
    """The booking credits the poster's own Kontokarte and needs a second person."""


class Line(NamedTuple):
    """One entry of a booking: `amount_cents` move from `credit` (Haben) to `debit` (Soll)."""

    debit: FinAccount
    credit: FinAccount
    amount_cents: int
    category: FinCategory | None = None
    memo: str | None = None


# --- Immutability ------------------------------------------------------------------


@event.listens_for(FinJournal, 'before_update')
@event.listens_for(FinEntry, 'before_update')
def _refuse_update(mapper, connection, target):
    raise FinanceError('Buchungen werden nie geändert; eine falsche Buchung wird storniert.')


@event.listens_for(FinJournal, 'before_delete')
@event.listens_for(FinEntry, 'before_delete')
def _refuse_delete(mapper, connection, target):
    raise FinanceError('Buchungen werden nie gelöscht; eine falsche Buchung wird storniert.')


# --- Books, accounts, cost centres -------------------------------------------------


def default_book():
    return db.session.scalar(select(FinBook).where(FinBook.key == DEFAULT_BOOK))


def account_by_key(key, book=None):
    book = book or default_book()
    return db.session.scalar(select(FinAccount).where(FinAccount.book_id == book.id, FinAccount.key == key))


def accounts(kinds=None, *, book=None, include_archived=False):
    """The book's accounts except Kontokarten, in display order."""
    book = book or default_book()
    query = select(FinAccount).where(FinAccount.book_id == book.id, FinAccount.kind != AccountKind.MEMBER)
    if kinds:
        query = query.where(FinAccount.kind.in_(kinds))
    if not include_archived:
        query = query.where(FinAccount.archived_at.is_(None))
    return db.session.scalars(query.order_by(FinAccount.sort_order, FinAccount.name)).all()


def categories(include_archived=False):
    query = select(FinCategory)
    if not include_archived:
        query = query.where(FinCategory.archived_at.is_(None))
    return db.session.scalars(query.order_by(FinCategory.sort_order, FinCategory.name)).all()


def member_account(member, *, book=None, create=True):
    """The member's Kontokarte; created on first use unless create=False (then None)."""
    book = book or default_book()
    account = db.session.scalar(
        select(FinAccount).where(FinAccount.book_id == book.id, FinAccount.member_id == member.id)
    )
    if account is None and create:
        account = FinAccount(book_id=book.id, kind=AccountKind.MEMBER, name=member.full_name, member_id=member.id)
        db.session.add(account)
        db.session.flush()
    return account


# --- Posting -----------------------------------------------------------------------


def find_by_idempotency_key(key):
    if not key:
        return None
    return db.session.scalar(select(FinJournal).where(FinJournal.idempotency_key == key))


def _check_lines(lines, book, *, allow_archived):
    if not lines:
        raise FinanceError('Eine Buchung braucht mindestens einen Betrag.')
    for line in lines:
        if isinstance(line.amount_cents, bool) or not isinstance(line.amount_cents, int) or line.amount_cents <= 0:
            raise FinanceError('Beträge müssen größer als 0 sein.')
        if line.debit is None or line.credit is None:
            raise FinanceError('Bitte Soll- und Haben-Konto wählen.')
        if line.debit.id == line.credit.id:
            raise FinanceError('Soll- und Haben-Konto müssen verschieden sein.')
        for account in (line.debit, line.credit):
            if account.book_id != book.id:
                raise FinanceError(f'Das Konto „{account.name}“ gehört zu einem anderen Buch.')
            if account.archived_at is not None and not allow_archived:
                raise FinanceError(f'Das Konto „{account.name}“ ist archiviert.')
        needs = [a.name for a in (line.debit, line.credit) if a.needs_cost_centre]
        if needs and line.category is None:
            raise FinanceError(f'Buchungen auf „{needs[0]}“ brauchen eine Kostenstelle.')


def _check_cosign(lines, posted_by, cosigned_by):
    """Rule 2 of §6.3: nobody credits their own Kontokarte alone."""
    if posted_by is None:  # server operator (CLI, import)
        return
    own = any(line.credit.kind == AccountKind.MEMBER and line.credit.member_id == posted_by.member_id for line in lines)
    if not own:
        return
    if cosigned_by is None:
        raise CosignRequired(
            'Diese Buchung schreibt dir selbst etwas gut. Sie braucht die Gegenzeichnung einer zweiten Person '
            '(Senior, Consenior oder Kassenprüfer).'
        )
    if cosigned_by.id == posted_by.id or cosigned_by.member_id == posted_by.member_id:
        raise CosignRequired('Gegenzeichnen muss eine andere Person.')
    if COSIGN_PERMISSION not in permissions_for(cosigned_by.member):
        raise CosignRequired(f'{cosigned_by.member.full_name} darf keine Buchungen gegenzeichnen.')


def post_journal(
    kind,
    title,
    booked_on,
    lines,
    *,
    posted_by,
    book=None,
    beleg=None,
    note=None,
    cosigned_by=None,
    idempotency_key=None,
    source=None,
    reverses=None,
    _allow_archived=False,
):
    """Save a booking. Returns (journal, created). Commits nothing.

    The same idempotency_key twice returns the first booking instead of a copy (double submit,
    a settlement posted again). source=(type, id) links the booking to what caused it.
    posted_by=None means the server operator (CLI, import).
    """
    existing = find_by_idempotency_key(idempotency_key)
    if existing is not None:
        return existing, False
    book = book or default_book()
    title = (title or '').strip()
    if not title:
        raise FinanceError('Bitte einen Buchungstext angeben.')
    if len(title) > TITLE_MAX:
        raise FinanceError(f'Der Buchungstext darf höchstens {TITLE_MAX} Zeichen haben.')
    if kind not in JournalKind.LABELS:
        raise FinanceError(f'Unbekannte Buchungsart {kind!r}.')
    if not isinstance(booked_on, date):
        raise FinanceError('Bitte ein Buchungsdatum angeben.')
    lines = list(lines)
    _check_lines(lines, book, allow_archived=_allow_archived)
    _check_cosign(lines, posted_by, cosigned_by)

    semester = current_semester(booked_on)
    journal = FinJournal(
        book_id=book.id,
        kind=kind,
        title=title,
        booked_on=booked_on,
        beleg=(beleg or '').strip() or None,
        semester_id=semester.id if semester else None,
        source_type=source[0] if source else None,
        source_id=source[1] if source else None,
        idempotency_key=idempotency_key,
        reverses_journal_id=reverses.id if reverses is not None else None,
        posted_by_user_id=getattr(posted_by, 'id', None),
        cosigned_by_user_id=getattr(cosigned_by, 'id', None),
        note=(note or '').strip() or None,
    )
    db.session.add(journal)
    db.session.flush()
    for line in lines:
        db.session.add(
            FinEntry(
                journal_id=journal.id,
                debit_account_id=line.debit.id,
                credit_account_id=line.credit.id,
                amount_cents=line.amount_cents,
                category_id=line.category.id if line.category is not None else None,
                memo=(line.memo or '').strip() or None,
            )
        )
    db.session.flush()
    db.session.expire(journal, ['entries'])
    audit.record(
        'finance.journal.post',
        journal,
        actor=posted_by,
        diff={
            'kind': kind,
            'title': title,
            'beleg': journal.beleg,
            'booked_on': booked_on,
            'total_cents': journal.total_cents,
            'entries': len(lines),
            'cosigned_by': journal.cosigned_by_user_id,
        },
    )
    return journal, True


def reverse(journal, *, reversed_by, reason, booked_on=None, cosigned_by=None):
    """Cancel a booking with a second one that moves every amount back. Returns the reversal."""
    reason = (reason or '').strip()
    if not reason:
        raise FinanceError('Bitte einen Grund für das Storno angeben.')
    if journal.kind == JournalKind.REVERSAL:
        raise FinanceError('Ein Storno kann nicht storniert werden; bitte die Buchung neu erfassen.')
    if journal.reversed_by is not None:
        raise FinanceError('Diese Buchung ist bereits storniert.')
    lines = [Line(e.credit_account, e.debit_account, e.amount_cents, e.category, e.memo) for e in journal.entries]
    reversal, _ = post_journal(
        JournalKind.REVERSAL,
        f'Storno: {journal.title}'[:TITLE_MAX],
        booked_on or today_local(),
        lines,
        posted_by=reversed_by,
        book=db.session.get(FinBook, journal.book_id),
        beleg=journal.beleg,
        note=reason,
        cosigned_by=cosigned_by,
        reverses=journal,
        _allow_archived=True,
    )
    db.session.expire(journal, ['reversed_by'])
    return reversal


# --- Balances and statements -------------------------------------------------------


def _sums(account_ids=None, *, until=None, book=None):
    """{account_id: (debit_cents, credit_cents)} over all bookings (up to `until`, inclusive)."""
    book = book or default_book()
    result = {}
    for column, index in ((FinEntry.debit_account_id, 0), (FinEntry.credit_account_id, 1)):
        query = (
            select(column, func.sum(FinEntry.amount_cents))
            .join(FinJournal, FinJournal.id == FinEntry.journal_id)
            .where(FinJournal.book_id == book.id)
            .group_by(column)
        )
        if account_ids is not None:
            query = query.where(column.in_(account_ids))
        if until is not None:
            query = query.where(FinJournal.booked_on <= until)
        for account_id, total in db.session.execute(query):
            pair = result.setdefault(account_id, [0, 0])
            pair[index] = total or 0
    return {key: tuple(value) for key, value in result.items()}


def account_balance(account, *, until=None):
    """Debits minus credits (the Excel's Soll minus Haben). For a Kontokarte use saldo()."""
    debit, credit = _sums([account.id], until=until).get(account.id, (0, 0))
    return debit - credit


def saldo(member, *, book=None, until=None):
    """The member's Saldo in cents: positive = credit, negative = owes the club. 0 without bookings."""
    account = member_account(member, book=book, create=False)
    if account is None:
        return 0
    debit, credit = _sums([account.id], until=until, book=book).get(account.id, (0, 0))
    return credit - debit


def member_saldi(*, book=None):
    """[(account, saldo_cents)] for every Kontokarte, by name. One query for all."""
    book = book or default_book()
    cards = db.session.scalars(
        select(FinAccount).where(FinAccount.book_id == book.id, FinAccount.kind == AccountKind.MEMBER)
    ).all()
    sums = _sums([card.id for card in cards], book=book)
    rows = [(card, sums.get(card.id, (0, 0))[1] - sums.get(card.id, (0, 0))[0]) for card in cards]
    return sorted(rows, key=lambda row: (row[0].member.last_name.lower(), row[0].member.first_name.lower()))


def account_balances(*, book=None):
    """[(account, debit_cents, credit_cents)] for every account except Kontokarten."""
    book = book or default_book()
    rows = accounts(book=book, include_archived=True)
    sums = _sums([account.id for account in rows], book=book)
    return [(account, *sums.get(account.id, (0, 0))) for account in rows]


class StatementRow(NamedTuple):
    journal: FinJournal
    entry: FinEntry
    other: FinAccount  # the account on the other side
    amount_cents: int  # signed from the account's view (Kontokarte: + credit, - debit)
    balance_cents: int  # after this entry


def statement(account, *, start=None, end=None):
    """(opening_cents, rows, closing_cents) for one account, oldest first.

    For a Kontokarte amounts are credits minus debits (what the member sees as their Saldo);
    for other accounts debits minus credits.
    """
    sign = 1 if account.kind == AccountKind.MEMBER else -1
    query = (
        select(FinEntry)
        .join(FinJournal, FinJournal.id == FinEntry.journal_id)
        .where((FinEntry.debit_account_id == account.id) | (FinEntry.credit_account_id == account.id))
        .order_by(FinJournal.booked_on, FinJournal.id, FinEntry.id)
    )
    opening = 0
    if start is not None:
        debit, credit = _sums([account.id], until=start - timedelta(days=1)).get(account.id, (0, 0))
        opening = sign * (credit - debit)
        query = query.where(FinJournal.booked_on >= start)
    if end is not None:
        query = query.where(FinJournal.booked_on <= end)
    rows, balance = [], opening
    for entry in db.session.scalars(query):
        credited = entry.credit_account_id == account.id
        amount = sign * (entry.amount_cents if credited else -entry.amount_cents)
        balance += amount
        other = entry.debit_account if credited else entry.credit_account
        rows.append(StatementRow(entry.journal, entry, other, amount, balance))
    return opening, rows, balance


def journals(*, book=None, start=None, end=None, account=None, category=None, limit=None):
    """Bookings newest first, optionally only those touching an account or cost centre."""
    book = book or default_book()
    query = select(FinJournal).where(FinJournal.book_id == book.id)
    if start is not None:
        query = query.where(FinJournal.booked_on >= start)
    if end is not None:
        query = query.where(FinJournal.booked_on <= end)
    if account is not None or category is not None:
        entries = select(FinEntry.journal_id)
        if account is not None:
            entries = entries.where(
                (FinEntry.debit_account_id == account.id) | (FinEntry.credit_account_id == account.id)
            )
        if category is not None:
            entries = entries.where(FinEntry.category_id == category.id)
        query = query.where(FinJournal.id.in_(entries))
    query = query.order_by(FinJournal.booked_on.desc(), FinJournal.id.desc())
    if limit:
        query = query.limit(limit)
    return db.session.scalars(query).all()


BELEG_RE = re.compile(r'^([A-Za-z]+)(\d+)$')


def next_beleg(prefix, booked_on, *, book=None):
    """The next free receipt number of a series in the semester of `booked_on`, e.g. G13."""
    book = book or default_book()
    semester = current_semester(booked_on)
    query = select(FinJournal.beleg).where(FinJournal.book_id == book.id, FinJournal.beleg.is_not(None))
    if semester is not None:
        query = query.where(FinJournal.semester_id == semester.id)
    else:
        query = query.where(FinJournal.semester_id.is_(None))
    highest = 0
    for beleg in db.session.scalars(query):
        match = BELEG_RE.match(beleg)
        if match and match.group(1).upper() == prefix.upper():
            highest = max(highest, int(match.group(2)))
    return f'{prefix.upper()}{highest + 1}'
