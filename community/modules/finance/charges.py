"""Charging many members at once (Umlage) and monthly charges (roadmap M3-4).

A share can be split: a Kasse (e.g. the Fuxenkasse) pays part of it for members of certain ranks,
so a Fux pays less while the event still receives the full amount.
Every Umlage is previewed first; posting recomputes the plan and is idempotent.
"""

from datetime import date
from typing import NamedTuple

from sqlalchemy import select

from community.core.identity.models import Member, MemberStatus
from community.core.org.services import current_rank_entries, group_members
from community.core.platform import audit
from community.core.time import format_month
from community.extensions import db

from . import services as ledger
from .models import FinRecurring, JournalKind


class Subsidy(NamedTuple):
    """`account` pays up to `cents` of each share of members whose rank is in `rank_ids`."""

    account: object
    rank_ids: frozenset
    cents: int


class Share(NamedTuple):
    member: Member
    rank: object  # Rank or None
    share_cents: int
    subsidy_cents: int

    @property
    def due_cents(self):
        return self.share_cents - self.subsidy_cents


def equal_shares(total_cents, member_ids):
    """{member_id: cents}, exact to the cent (largest remainder, ties by member id; D20)."""
    if total_cents <= 0 or not member_ids:
        raise ledger.FinanceError('Betrag und Teilnehmer fehlen.')
    ids = sorted(member_ids)
    base, rest = divmod(total_cents, len(ids))
    return {member_id: base + (1 if index < rest else 0) for index, member_id in enumerate(ids)}


def audience(day, *, rank_ids=(), group=None, member_ids=()):
    """Active members on `day` with one of the ranks, in the group, or picked by hand; by name."""
    ranks = current_rank_entries(day)
    chosen = set(member_ids)
    if rank_ids:
        chosen |= {member_id for member_id, entry in ranks.items() if entry.rank_id in set(rank_ids)}
    if group is not None:
        chosen |= {membership.member_id for membership in group_members(group, day)}
    if not chosen:
        return []
    members = db.session.scalars(
        select(Member).where(Member.id.in_(chosen), Member.status == MemberStatus.ACTIVE)
    ).all()
    return sorted(members, key=lambda m: (m.last_name.lower(), m.first_name.lower()))


def plan(members, day, *, per_person_cents=None, total_cents=None, subsidy=None):
    """The shares of an Umlage, before posting. Exactly one of per_person_cents / total_cents."""
    if not members:
        raise ledger.FinanceError('Bitte mindestens eine Person auswählen.')
    if (per_person_cents is None) == (total_cents is None):
        raise ledger.FinanceError('Bitte Betrag pro Person oder Gesamtbetrag angeben.')
    if per_person_cents is not None:
        if per_person_cents <= 0:
            raise ledger.FinanceError('Der Betrag muss größer als 0 sein.')
        shares = {member.id: per_person_cents for member in members}
    else:
        shares = equal_shares(total_cents, [member.id for member in members])
    ranks = current_rank_entries(day)
    result = []
    for member in members:
        entry = ranks.get(member.id)
        rank = entry.rank if entry else None
        share = shares[member.id]
        covered = 0
        if subsidy is not None and rank is not None and rank.id in subsidy.rank_ids:
            covered = min(subsidy.cents, share)
        result.append(Share(member, rank, share, covered))
    return result


def totals(shares):
    return {
        'share': sum(s.share_cents for s in shares),
        'subsidy': sum(s.subsidy_cents for s in shares),
        'due': sum(s.due_cents for s in shares),
        'people': len(shares),
    }


def post(
    shares,
    *,
    title,
    booked_on,
    target,
    category,
    posted_by,
    subsidy=None,
    beleg=None,
    idempotency_key=None,
    kind=JournalKind.SPLIT,
    source=None,
):
    """Book the planned shares as one booking: each member's part, and the Kasse's parts."""
    lines = []
    for share in shares:
        if share.due_cents > 0:
            lines.append(ledger.Line(ledger.member_account(share.member), target, share.due_cents, category))
        if share.subsidy_cents > 0:
            memo = f'Anteil {share.member.full_name}'
            lines.append(ledger.Line(subsidy.account, target, share.subsidy_cents, category, memo))
    journal, created = ledger.post_journal(
        kind,
        title,
        booked_on,
        lines,
        posted_by=posted_by,
        beleg=beleg,
        idempotency_key=idempotency_key,
        source=source,
    )
    return journal, created


# --- Monthly charges --------------------------------------------------------------------


def month_label(day):
    return format_month(day)


def recurring_key(recurring, day):
    return f'recurring:{recurring.id}:{day.year:04d}-{day.month:02d}'


def recurring_items(include_archived=False):
    query = select(FinRecurring)
    if not include_archived:
        query = query.where(FinRecurring.archived_at.is_(None))
    return db.session.scalars(query.order_by(FinRecurring.title)).all()


def recurring_rank_ids(recurring):
    from community.core.org.services import rank_by_key

    ranks = [rank_by_key(key) for key in recurring.rank_key_list]
    return [rank.id for rank in ranks if rank is not None]


def recurring_plan(recurring, day):
    members = audience(day, rank_ids=recurring_rank_ids(recurring), group=recurring.group)
    return plan(members, day, per_person_cents=recurring.amount_cents) if members else []


def recurring_posted(recurring, day):
    return ledger.find_by_idempotency_key(recurring_key(recurring, day))


def post_recurring(recurring, day, *, posted_by):
    """Book one month of a monthly charge. Returns (journal, created); a month is booked once."""
    day = date(day.year, day.month, 1)
    existing = recurring_posted(recurring, day)
    if existing is not None:
        return existing, False
    shares = recurring_plan(recurring, day)
    if not shares:
        raise ledger.FinanceError('Niemand gehört an diesem Tag zur Zielgruppe.')
    return post(
        shares,
        title=f'{recurring.title} {month_label(day)}',
        booked_on=day,
        target=recurring.target_account,
        category=recurring.category,
        posted_by=posted_by,
        idempotency_key=recurring_key(recurring, day),
        kind=JournalKind.CHARGE,
        source=('recurring', recurring.id),
    )


def save_recurring(recurring, *, title, amount_cents, target, category, rank_keys, group, actor):
    """Create (recurring=None) or change a monthly charge. Commits nothing."""
    title = (title or '').strip()
    if not title:
        raise ledger.FinanceError('Bitte einen Titel angeben.')
    if amount_cents <= 0:
        raise ledger.FinanceError('Der Betrag muss größer als 0 sein.')
    if not rank_keys and group is None:
        raise ledger.FinanceError('Bitte Ränge oder eine Gruppe als Zielgruppe wählen.')
    if target.needs_cost_centre and category is None:
        raise ledger.FinanceError(f'Buchungen auf „{target.name}“ brauchen eine Kostenstelle.')
    created = recurring is None
    if created:
        recurring = FinRecurring(title=title, amount_cents=amount_cents, target_account_id=target.id)
        db.session.add(recurring)
    fields = ('title', 'amount_cents', 'target_account_id', 'category_id', 'rank_keys', 'group_id')
    before = {} if created else audit.snapshot(recurring, fields)
    recurring.title = title
    recurring.amount_cents = amount_cents
    recurring.target_account_id = target.id
    recurring.category_id = category.id if category is not None else None
    recurring.rank_keys = ','.join(sorted(rank_keys))
    recurring.group_id = group.id if group is not None else None
    db.session.flush()
    diff = audit.changes(recurring, fields, before)
    audit.record(
        'finance.recurring.create' if created else 'finance.recurring.update', recurring, actor=actor, diff=diff
    )
    return recurring
