"""Data for the start page that belongs to no single module: the greeting line, news from the
Bund and the Bund in numbers."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select

from community.core.identity.models import Member, MemberStatus
from community.core.org import services as org
from community.core.org.models import GroupKind, OfficeAssignment, RankHistory
from community.core.platform.notify import Notification
from community.core.time import today_local
from community.extensions import db

NEWS_DAYS = 30
NEWS_LIMIT = 6


@dataclass(frozen=True)
class Header:
    rank: object  # Rank or None
    offices: list  # [OfficeAssignment]
    semester: object  # Semester or None
    week: int | None  # week of the semester, 1 = the first


def header(member, day=None):
    day = day or today_local()
    semester = org.current_semester(day)
    week = (day - semester.starts_on).days // 7 + 1 if semester else None
    return Header(org.current_rank(member, day), org.member_offices(member, day), semester, week)


@dataclass(frozen=True)
class News:
    day: object
    member: Member
    text: str


def bund_news(day=None, *, days=NEWS_DAYS, limit=NEWS_LIMIT):
    """What changed in the Bund lately: new members, new ranks, new Amtsträger. Newest first."""
    day = day or today_local()
    since = day - timedelta(days=days)
    items = []
    first_rank = select(RankHistory.member_id, func.min(RankHistory.valid_from).label('first')).group_by(
        RankHistory.member_id
    )
    first_rank = {member_id: first for member_id, first in db.session.execute(first_rank)}
    ranks = db.session.scalars(
        select(RankHistory)
        .join(Member, Member.id == RankHistory.member_id)
        .where(RankHistory.valid_from > since, RankHistory.valid_from <= day, Member.status == MemberStatus.ACTIVE)
    )
    for entry in ranks:
        member = db.session.get(Member, entry.member_id)
        if first_rank.get(entry.member_id) == entry.valid_from:
            items.append(News(entry.valid_from, member, f'Neu im Bund: {member.display_name} ({entry.rank.label})'))
        else:
            items.append(News(entry.valid_from, member, f'{member.display_name} ist jetzt {entry.rank.label}'))
    offices = db.session.scalars(
        select(OfficeAssignment).where(OfficeAssignment.valid_from > since, OfficeAssignment.valid_from <= day)
    )
    for assignment in offices:
        if assignment.member.is_active_member:
            text = f'{assignment.member.display_name} ist {assignment.office.label}'
            items.append(
                News(
                    assignment.valid_from,
                    assignment.member,
                    text + (' (kommissarisch)' if assignment.is_acting else ''),
                )
            )
    items.sort(key=lambda item: item.day, reverse=True)
    return items[:limit]


def bund_numbers(day=None):
    """[(label, count)]: active members by rank (in rank order), then the residents."""
    day = day or today_local()
    active = set(db.session.scalars(select(Member.id).where(Member.status == MemberStatus.ACTIVE)))
    counts = {}
    for member_id, entry in org.current_rank_entries(day).items():
        if member_id in active:
            counts[entry.rank] = counts.get(entry.rank, 0) + 1
    rows = [(rank.label, counts[rank]) for rank in sorted(counts, key=lambda r: r.sort_order)]
    for group in org.groups():
        if group.kind == GroupKind.RESIDENCE:
            residents = len(org.group_members(group, day))
            if residents:
                rows.append((group.name, residents))
    return rows, len(active)


def unread_notifications(member, limit=3):
    return db.session.scalars(
        select(Notification)
        .where(Notification.member_id == member.id, Notification.read_at.is_(None))
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    ).all()
