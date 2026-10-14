"""Push-TAN (M3-8): a booking that credits the poster's own Kontokarte waits for a second person.

The Kassenwart prepares it (request_booking, request_reversal) and nothing is booked yet. Every
member who holds finance.journal.cosign (the Kassenprüfer), except the requester, is notified.
One of them confirms it, and the booking is posted exactly once with them as cosigner, or
rejects it with a reason, and nothing is booked. Nobody confirms their own request.
"""

from flask import url_for
from sqlalchemy import select, update

from community.core.authz.service import holders_of, permissions_for
from community.core.money import format_cents
from community.core.platform import audit
from community.core.platform.notify import declare_kind, notify
from community.core.time import today_local, utcnow
from community.extensions import db

from .models import FinAccount, FinBook, FinCategory, FinRequest, JournalKind, RequestStatus
from .services import (
    COSIGN_PERMISSION,
    TITLE_MAX,
    FinanceError,
    Line,
    check_booking,
    check_reversible,
    credits_own_card,
    default_book,
    post_journal,
    reversal_lines,
    reverse,
)

REQUEST_KIND = declare_kind('finance_cosign', 'Buchung zur Gegenzeichnung (für den Kassenprüfer)')
DECIDED_KIND = declare_kind('finance_cosign_decided', 'Gegenzeichnung bestätigt oder abgelehnt')
REASON_MAX = 500


def cosigners(exclude=None):
    """Active members who may confirm a request; `exclude` (the requester, a User) is left out."""
    return [m for m in holders_of(COSIGN_PERMISSION) if exclude is None or m.id != exclude.member_id]


def open_requests():
    return db.session.scalars(
        select(FinRequest).where(FinRequest.status == RequestStatus.OPEN).order_by(FinRequest.requested_at)
    ).all()


def decided_requests(limit=20):
    query = select(FinRequest).where(FinRequest.status != RequestStatus.OPEN)
    return db.session.scalars(query.order_by(FinRequest.decided_at.desc()).limit(limit)).all()


def request_lines(item):
    """The lines the request will book, with accounts and cost centres looked up again."""
    if item.reverses_journal_id is not None:
        return reversal_lines(item.reverses)
    lines = []
    for data in item.lines_json:
        debit, credit = db.session.get(FinAccount, data['debit']), db.session.get(FinAccount, data['credit'])
        category = db.session.get(FinCategory, data['category']) if data.get('category') else None
        if debit is None or credit is None or (data.get('category') and category is None):
            raise FinanceError('Ein Konto oder eine Kostenstelle dieser Anfrage gibt es nicht mehr.')
        lines.append(Line(debit, credit, data['amount_cents'], category, data.get('memo')))
    return lines


def request_total(item):
    return sum(line.amount_cents for line in request_lines(item))


def _line_data(line):
    return {
        'debit': line.debit.id,
        'credit': line.credit.id,
        'amount_cents': line.amount_cents,
        'category': line.category.id if line.category is not None else None,
        'memo': (line.memo or '').strip() or None,
    }


def _needs_request(lines, requested_by):
    if not credits_own_card(lines, requested_by):
        raise FinanceError('Diese Buchung braucht keine Gegenzeichnung; bitte direkt buchen.')


def request_booking(kind, title, booked_on, lines, *, requested_by, beleg=None, note=None, book=None):
    """Prepare a booking for confirmation. Returns (request, created); the same open request twice
    (a double click) returns the first. Commits nothing."""
    book = book or default_book()
    lines = list(lines)
    title = check_booking(kind, title, booked_on, lines, book)
    _needs_request(lines, requested_by)
    fields = {
        'book_id': book.id,
        'kind': kind,
        'title': title,
        'booked_on': booked_on,
        'beleg': (beleg or '').strip() or None,
        'note': (note or '').strip() or None,
        'lines_json': [_line_data(line) for line in lines],
    }
    for item in open_requests():
        if item.requested_by_user_id == requested_by.id and all(getattr(item, k) == v for k, v in fields.items()):
            return item, False
    return _create(fields, requested_by, lines), True


def request_reversal(journal, *, requested_by, reason, booked_on=None):
    """Prepare the reversal of `journal` for confirmation. Returns (request, created). Commits nothing."""
    reason = check_reversible(journal, reason)
    lines = reversal_lines(journal)
    _needs_request(lines, requested_by)
    existing = db.session.scalar(
        select(FinRequest).where(FinRequest.reverses_journal_id == journal.id, FinRequest.status == RequestStatus.OPEN)
    )
    if existing is not None:
        return existing, False
    fields = {
        'book_id': journal.book_id,
        'kind': JournalKind.REVERSAL,
        'title': f'Storno: {journal.title}'[:TITLE_MAX],
        'booked_on': booked_on or today_local(),
        'beleg': journal.beleg,
        'note': reason,
        'reverses_journal_id': journal.id,
    }
    return _create(fields, requested_by, lines), True


def _create(fields, requested_by, lines):
    receivers = cosigners(exclude=requested_by)
    if not receivers:
        raise FinanceError('Niemand außer dir darf gegenzeichnen. Bitte zuerst einen Kassenprüfer eintragen lassen.')
    item = FinRequest(requested_by_user_id=requested_by.id, status=RequestStatus.OPEN, **fields)
    db.session.add(item)
    db.session.flush()
    total = sum(line.amount_cents for line in lines)
    audit.record(
        'finance.request.create',
        item,
        actor=requested_by,
        diff={'title': item.title, 'kind': item.kind, 'total_cents': total, 'reverses': item.reverses_journal_id},
    )
    link = url_for('finance.cosign_request', request_id=item.id)
    title = f'{requested_by.member.full_name} bittet um Gegenzeichnung: {item.title} ({format_cents(total)})'
    for member in receivers:
        notify(member, REQUEST_KIND, title, link=link)
    return item


def _check_decider(item, user):
    if not item.is_open:
        raise FinanceError('Diese Anfrage ist bereits entschieden.')
    if user.id == item.requested_by_user_id or user.member_id == item.requested_by.member_id:
        raise FinanceError('Deine eigene Anfrage muss eine andere Person gegenzeichnen.')
    if COSIGN_PERMISSION not in permissions_for(user.member):
        raise FinanceError('Du darfst keine Buchungen gegenzeichnen.')


def _close(item, status, user, *, journal=None, reason=None):
    """Move the request out of 'open' exactly once, even if two people decide at the same time."""
    result = db.session.execute(
        update(FinRequest)
        .where(FinRequest.id == item.id, FinRequest.status == RequestStatus.OPEN)
        .values(
            status=status,
            decided_by_user_id=user.id,
            decided_at=utcnow(),
            journal_id=journal.id if journal is not None else None,
            reject_reason=reason,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise FinanceError('Diese Anfrage ist bereits entschieden.')
    db.session.expire(item)


def confirm(item, *, confirmed_by):
    """Book the request with `confirmed_by` as cosigner. Returns the booking. Commits nothing."""
    _check_decider(item, confirmed_by)
    requester = item.requested_by
    needed = 'finance.journal.reverse' if item.reverses_journal_id is not None else 'finance.journal.post'
    if needed not in permissions_for(requester.member):
        raise FinanceError(f'{requester.member.full_name} darf nicht mehr buchen. Bitte die Anfrage ablehnen.')
    if item.reverses_journal_id is not None:
        journal = reverse(
            item.reverses,
            reversed_by=requester,
            reason=item.note,
            booked_on=item.booked_on,
            cosigned_by=confirmed_by,
        )
    else:
        journal, _ = post_journal(
            item.kind,
            item.title,
            item.booked_on,
            request_lines(item),
            posted_by=requester,
            book=db.session.get(FinBook, item.book_id),
            beleg=item.beleg,
            note=item.note,
            cosigned_by=confirmed_by,
            idempotency_key=f'request:{item.id}',
            source=('fin_request', item.id),
        )
    _close(item, RequestStatus.CONFIRMED, confirmed_by, journal=journal)
    audit.record('finance.request.confirm', item, actor=confirmed_by, diff={'journal': journal.id})
    notify(
        requester.member,
        DECIDED_KIND,
        f'{confirmed_by.member.full_name} hat gegengezeichnet: {journal.title}',
        link=url_for('finance.booking', journal_id=journal.id),
    )
    return journal


def reject(item, *, rejected_by, reason):
    """Close the request without booking anything. Commits nothing."""
    reason = (reason or '').strip()
    if not reason:
        raise FinanceError('Bitte einen Grund für die Ablehnung angeben.')
    if len(reason) > REASON_MAX:
        raise FinanceError(f'Der Grund darf höchstens {REASON_MAX} Zeichen haben.')
    _check_decider(item, rejected_by)
    _close(item, RequestStatus.REJECTED, rejected_by, reason=reason)
    audit.record('finance.request.reject', item, actor=rejected_by, diff={'reason': reason})
    notify(
        item.requested_by.member,
        DECIDED_KIND,
        f'{rejected_by.member.full_name} hat abgelehnt: {item.title}. Grund: {reason}',
        link=url_for('finance.cosign_request', request_id=item.id),
    )
