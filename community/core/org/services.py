"""Semesters and ranks: the rules (who may change what, which dates are valid)."""

from datetime import date, timedelta

from sqlalchemy import select

from community.core.authz.service import active_on, clear_cache, role_source
from community.core.platform import audit
from community.core.time import today_local
from community.extensions import db

from .models import Rank, RankHistory, Semester, SemesterKind


class OrgError(Exception):
    """A rule was broken; the message is shown to the user (German)."""


# --- Semesters -------------------------------------------------------------------


def semester_for(day):
    """The standard semester containing `day`: SS = 1 Apr - 30 Sep, WS = 1 Oct - 31 Mar."""
    if 4 <= day.month <= 9:
        return {
            'code': f'SS{day.year % 100:02d}',
            'kind': SemesterKind.SUMMER,
            'starts_on': date(day.year, 4, 1),
            'ends_on': date(day.year, 9, 30),
        }
    start_year = day.year if day.month >= 10 else day.year - 1
    return {
        'code': f'WS{start_year % 100:02d}/{(start_year + 1) % 100:02d}',
        'kind': SemesterKind.WINTER,
        'starts_on': date(start_year, 10, 1),
        'ends_on': date(start_year + 1, 3, 31),
    }


def suggest_next_semester():
    """The first standard semester after the latest one in the database (or the current one)."""
    latest = db.session.scalar(select(Semester).order_by(Semester.ends_on.desc()).limit(1))
    return semester_for(latest.ends_on + timedelta(days=1) if latest else today_local())


def current_semester(day=None):
    day = day or today_local()
    return db.session.scalar(select(Semester).where(Semester.starts_on <= day, Semester.ends_on >= day))


def create_semester(code, kind, starts_on, ends_on, *, status=None):
    if starts_on >= ends_on:
        raise OrgError('Das Semester muss vor seinem Ende beginnen.')
    if db.session.scalar(select(Semester.id).where(Semester.code == code)):
        raise OrgError(f'Das Semester {code} gibt es schon.')
    overlap = db.session.scalar(
        select(Semester.code).where(Semester.starts_on <= ends_on, Semester.ends_on >= starts_on).limit(1)
    )
    if overlap:
        raise OrgError(f'Überschneidet sich mit {overlap}.')
    semester = Semester(code=code, kind=kind, starts_on=starts_on, ends_on=ends_on)
    if status:
        semester.status = status
    db.session.add(semester)
    db.session.flush()
    audit.record('org.semester.create', semester, diff={'code': code, 'starts_on': starts_on, 'ends_on': ends_on})
    return semester


# --- Ranks -----------------------------------------------------------------------


def ranks(include_archived=False):
    query = select(Rank).order_by(Rank.sort_order)
    if not include_archived:
        query = query.where(Rank.archived_at.is_(None))
    return db.session.scalars(query).all()


def rank_by_key(key):
    return db.session.scalar(select(Rank).where(Rank.key == key))


def current_rank_entry(member, day=None):
    day = day or today_local()
    return db.session.scalar(select(RankHistory).where(RankHistory.member_id == member.id, active_on(RankHistory, day)))


def current_rank(member, day=None):
    entry = current_rank_entry(member, day)
    return entry.rank if entry else None


def rank_history(member):
    return db.session.scalars(
        select(RankHistory).where(RankHistory.member_id == member.id).order_by(RankHistory.valid_from.desc())
    ).all()


@role_source
def _rank_role(member, day):
    entry = current_rank_entry(member, day)
    return {entry.rank.role_id} if entry else set()


# Called with (member, new_rank, valid_from, changed_by) after a rank change; M1-5 uses it to
# handle Ämter that the new rank cannot hold.
RANK_CHANGE_HOOKS = []


def change_rank(member, rank, valid_from, *, changed_by=None, convent_ref=None, note=None, **hook_options):
    """Put a member into `rank` from `valid_from` on. Commits nothing.

    changed_by=None means the server operator (CLI); otherwise nobody may change their own rank.
    """
    if changed_by is not None and changed_by.member_id == member.id:
        raise OrgError('Den eigenen Rang kann man nicht selbst ändern.')
    if rank.archived_at is not None:
        raise OrgError('Dieser Rang wird nicht mehr verwendet.')
    open_entry = db.session.scalar(
        select(RankHistory).where(RankHistory.member_id == member.id, RankHistory.valid_to.is_(None))
    )
    if open_entry is not None:
        if open_entry.rank_id == rank.id:
            raise OrgError(f'{member.full_name} ist bereits {rank.label}.')
        if valid_from <= open_entry.valid_from:
            raise OrgError(
                f'Das Datum muss nach dem Beginn des aktuellen Rangs ({open_entry.valid_from:%d.%m.%Y}) liegen.'
            )
        open_entry.valid_to = valid_from - timedelta(days=1)
        db.session.flush()
    entry = RankHistory(
        member_id=member.id,
        rank_id=rank.id,
        valid_from=valid_from,
        convent_ref=convent_ref or None,
        note=note or None,
        changed_by_user_id=getattr(changed_by, 'id', None),
    )
    db.session.add(entry)
    db.session.flush()
    audit.record(
        'org.rank.change',
        member,
        actor=changed_by,
        diff={
            'from': open_entry.rank.key if open_entry else None,
            'to': rank.key,
            'valid_from': valid_from,
            'convent_ref': convent_ref,
        },
    )
    for hook in RANK_CHANGE_HOOKS:
        hook(member, rank, valid_from, changed_by, **hook_options)
    clear_cache()
    return entry
