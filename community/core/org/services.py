"""Semesters and ranks: the rules (who may change what, which dates are valid)."""

from datetime import date, timedelta

from sqlalchemy import or_, select

from community.core.authz.service import active_on, clear_cache, role_source
from community.core.platform import audit
from community.core.time import today_local
from community.extensions import db

from .models import (
    GroupKind,
    GroupMembership,
    MemberGroup,
    Office,
    OfficeAssignment,
    OfficeIncompatibility,
    Rank,
    RankHistory,
    Semester,
    SemesterKind,
)


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


def current_rank_entries(day=None):
    """{member_id: RankHistory} of everyone's rank on `day`, in one query (for lists)."""
    day = day or today_local()
    return {
        entry.member_id: entry for entry in db.session.scalars(select(RankHistory).where(active_on(RankHistory, day)))
    }


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


# --- Ämter -----------------------------------------------------------------------


def offices(include_archived=False):
    query = select(Office).order_by(Office.sort_order)
    if not include_archived:
        query = query.where(Office.archived_at.is_(None))
    return db.session.scalars(query).all()


def office_by_key(key):
    return db.session.scalar(select(Office).where(Office.key == key))


def overlaps_from(model, day):
    """SQL condition: the period is still running on or after `day`."""
    return or_(model.valid_to.is_(None), model.valid_to >= day)


def office_holders(office, day=None):
    day = day or today_local()
    return db.session.scalars(
        select(OfficeAssignment)
        .where(OfficeAssignment.office_id == office.id, active_on(OfficeAssignment, day))
        .order_by(OfficeAssignment.valid_from)
    ).all()


def member_offices(member, day=None):
    day = day or today_local()
    return db.session.scalars(
        select(OfficeAssignment)
        .where(OfficeAssignment.member_id == member.id, active_on(OfficeAssignment, day))
        .order_by(OfficeAssignment.valid_from)
    ).all()


def office_assignments_on(day=None):
    """All Ämter held on `day`, in office order, in one query (for lists)."""
    day = day or today_local()
    return db.session.scalars(
        select(OfficeAssignment)
        .join(Office, Office.id == OfficeAssignment.office_id)
        .where(active_on(OfficeAssignment, day))
        .order_by(Office.sort_order, OfficeAssignment.valid_from)
    ).all()


def office_history(office):
    return db.session.scalars(
        select(OfficeAssignment)
        .where(OfficeAssignment.office_id == office.id)
        .order_by(OfficeAssignment.valid_from.desc(), OfficeAssignment.id.desc())
    ).all()


def incompatible_office_ids(office):
    rows = db.session.execute(
        select(OfficeIncompatibility.office_a_id, OfficeIncompatibility.office_b_id).where(
            or_(OfficeIncompatibility.office_a_id == office.id, OfficeIncompatibility.office_b_id == office.id)
        )
    ).all()
    return {a if b == office.id else b for a, b in rows}


def is_eligible(office, member, day):
    rank = current_rank(member, day)
    return rank is not None and rank.id in {r.id for r in office.eligible_ranks}


def assign_office(
    office,
    member,
    valid_from,
    *,
    assigned_by=None,
    is_acting=False,
    semester=None,
    elected_on=None,
    convent_ref=None,
    end_previous=True,
):
    """Give `member` the Amt from `valid_from` on (until the successor takes over). Commits nothing.

    end_previous: if the Amt is full, the current holders' terms end the day before.
    assigned_by=None means the server operator (CLI); otherwise nobody may assign themselves.
    """
    from community.core.identity.models import MemberStatus

    if assigned_by is not None and assigned_by.member_id == member.id:
        raise OrgError('Ein Amt kann man sich nicht selbst geben.')
    if office.archived_at is not None:
        raise OrgError('Dieses Amt wird nicht mehr verwendet.')
    if member.status != MemberStatus.ACTIVE:
        raise OrgError(f'{member.full_name} ist kein aktives Mitglied.')
    if not is_acting and not is_eligible(office, member, valid_from):
        labels = ', '.join(r.label for r in office.eligible_ranks) or 'niemand'
        raise OrgError(f'{office.label} können nur {labels} übernehmen. Ausnahme: kommissarisch (wird protokolliert).')
    running = db.session.scalars(
        select(OfficeAssignment).where(
            OfficeAssignment.member_id == member.id, overlaps_from(OfficeAssignment, valid_from)
        )
    ).all()
    if any(a.office_id == office.id for a in running):
        raise OrgError(f'{member.full_name} hat das Amt {office.label} in diesem Zeitraum bereits.')
    conflicts = incompatible_office_ids(office)
    clash = next((a for a in running if a.office_id in conflicts), None)
    if clash is not None:
        raise OrgError(f'{office.label} und {clash.office.label} darf niemand gleichzeitig haben.')

    holders = db.session.scalars(
        select(OfficeAssignment).where(
            OfficeAssignment.office_id == office.id, overlaps_from(OfficeAssignment, valid_from)
        )
    ).all()
    if len(holders) >= office.max_holders:
        if not end_previous:
            raise OrgError(f'{office.label} ist besetzt. Bisherige Inhaber zuerst beenden.')
        later = [a for a in holders if a.valid_from >= valid_from]
        if later:
            raise OrgError(f'Es gibt schon eine Besetzung ab {later[0].valid_from:%d.%m.%Y}.')
        excess = len(holders) - office.max_holders + 1
        for previous in sorted(holders, key=lambda a: a.valid_from)[:excess]:
            end_assignment(
                previous, valid_from - timedelta(days=1), reason='Nachfolger übernimmt', ended_by=assigned_by
            )

    assignment = OfficeAssignment(
        office_id=office.id,
        member_id=member.id,
        semester_id=getattr(semester, 'id', None),
        valid_from=valid_from,
        is_acting=is_acting,
        elected_on=elected_on,
        convent_ref=convent_ref or None,
        assigned_by_user_id=getattr(assigned_by, 'id', None),
    )
    db.session.add(assignment)
    db.session.flush()
    audit.record(
        'org.office.assign',
        assignment,
        actor=assigned_by,
        diff={
            'office': office.key,
            'member_id': member.id,
            'valid_from': valid_from,
            'is_acting': is_acting,
            'eligible': is_eligible(office, member, valid_from),
        },
    )
    clear_cache()
    return assignment


def end_assignment(assignment, on, *, reason=None, ended_by=None):
    """End an Amt on `on` (inclusive). Commits nothing."""
    if on < assignment.valid_from:
        raise OrgError(f'Das Ende darf nicht vor dem Beginn ({assignment.valid_from:%d.%m.%Y}) liegen.')
    if assignment.valid_to is not None and assignment.valid_to <= on:
        return assignment
    assignment.valid_to = on
    assignment.end_reason = reason or assignment.end_reason
    audit.record(
        'org.office.end',
        assignment,
        actor=ended_by,
        diff={'office': assignment.office.key, 'member_id': assignment.member_id, 'valid_to': on, 'reason': reason},
    )
    clear_cache()
    return assignment


@role_source
def _office_roles(member, day):
    rows = db.session.scalars(
        select(Office.role_id)
        .join(OfficeAssignment, OfficeAssignment.office_id == Office.id)
        .where(OfficeAssignment.member_id == member.id, active_on(OfficeAssignment, day))
    )
    return set(rows)


def _offices_after_rank_change(member, rank, valid_from, changed_by, offices='close', **_):
    """Ämter the new rank may not hold: end them the day before, or keep them as acting (audited)."""
    for assignment in member_offices(member, valid_from):
        if rank.id in {r.id for r in assignment.office.eligible_ranks}:
            continue
        if offices == 'keep_acting':
            assignment.is_acting = True
            audit.record(
                'org.office.keep_acting',
                assignment,
                actor=changed_by,
                diff={'office': assignment.office.key, 'new_rank': rank.key},
            )
        else:
            end_assignment(
                assignment,
                max(valid_from - timedelta(days=1), assignment.valid_from),
                reason=f'Rangwechsel zu {rank.label}',
                ended_by=changed_by,
            )


RANK_CHANGE_HOOKS.append(_offices_after_rank_change)


# --- Groups ----------------------------------------------------------------------


def groups(include_archived=False):
    query = select(MemberGroup).order_by(MemberGroup.kind, MemberGroup.name)
    if not include_archived:
        query = query.where(MemberGroup.archived_at.is_(None))
    return db.session.scalars(query).all()


def create_group(name, kind=GroupKind.TASK, *, semester=None, created_by=None):
    name = name.strip()
    exists = db.session.scalar(
        select(MemberGroup.id).where(MemberGroup.name == name, MemberGroup.archived_at.is_(None))
    )
    if exists:
        raise OrgError(f'Die Gruppe {name} gibt es schon.')
    group = MemberGroup(name=name, kind=kind, semester_id=getattr(semester, 'id', None))
    db.session.add(group)
    db.session.flush()
    audit.record('org.group.create', group, actor=created_by, diff={'name': name, 'kind': kind})
    return group


def group_members(group, day=None):
    day = day or today_local()
    return db.session.scalars(
        select(GroupMembership)
        .where(GroupMembership.group_id == group.id, active_on(GroupMembership, day))
        .order_by(GroupMembership.valid_from)
    ).all()


def member_groups(member, day=None):
    day = day or today_local()
    return db.session.scalars(
        select(GroupMembership).where(GroupMembership.member_id == member.id, active_on(GroupMembership, day))
    ).all()


def add_to_group(group, member, valid_from, *, is_lead=False, added_by=None):
    from community.core.identity.models import MemberStatus

    if member.status != MemberStatus.ACTIVE:
        raise OrgError(f'{member.full_name} ist kein aktives Mitglied.')
    running = db.session.scalar(
        select(GroupMembership.id).where(
            GroupMembership.group_id == group.id,
            GroupMembership.member_id == member.id,
            overlaps_from(GroupMembership, valid_from),
        )
    )
    if running:
        raise OrgError(f'{member.full_name} ist bereits in {group.name}.')
    membership = GroupMembership(group_id=group.id, member_id=member.id, valid_from=valid_from, is_lead=is_lead)
    db.session.add(membership)
    db.session.flush()
    audit.record('org.group.add', membership, actor=added_by, diff={'group': group.name, 'member_id': member.id})
    clear_cache()
    return membership


def end_membership(membership, on, *, ended_by=None):
    if on < membership.valid_from:
        on = membership.valid_from
    membership.valid_to = on
    audit.record(
        'org.group.remove',
        membership,
        actor=ended_by,
        diff={'group': membership.group.name, 'member_id': membership.member_id, 'valid_to': on},
    )
    clear_cache()
    return membership


@role_source
def _group_roles(member, day):
    rows = db.session.scalars(
        select(MemberGroup.role_id)
        .join(GroupMembership, GroupMembership.group_id == MemberGroup.id)
        .where(
            GroupMembership.member_id == member.id,
            MemberGroup.role_id.is_not(None),
            MemberGroup.archived_at.is_(None),
            active_on(GroupMembership, day),
        )
    )
    return set(rows)


# --- Austritt --------------------------------------------------------------------


def _last_day(period, on):
    """The day a period has to end when the member leaves on `on`: that day, or its own first
    day if it starts later (valid_to >= valid_from)."""
    return max(on, period.valid_from)


def _running_after(model, member, on):
    rows = db.session.scalars(
        select(model).where(model.member_id == member.id, overlaps_from(model, on)).order_by(model.valid_from, model.id)
    ).all()
    return [row for row in rows if row.valid_to is None or row.valid_to > _last_day(row, on)]


def end_member_periods(member, on, *, reason=None, ended_by=None):
    """End the rank, Ämter and group memberships of `member` still running after `on` (Austritt,
    roadmap §6.4), so successors are not blocked by max_holders. Commits nothing.

    Returns what was ended: {'rank': [rank keys], 'offices': [office keys], 'groups': [names]}.
    """
    ended = {'rank': [], 'offices': [], 'groups': []}
    for entry in _running_after(RankHistory, member, on):
        entry.valid_to = _last_day(entry, on)
        ended['rank'].append(entry.rank.key)
    for assignment in _running_after(OfficeAssignment, member, on):
        end_assignment(assignment, _last_day(assignment, on), reason=reason, ended_by=ended_by)
        ended['offices'].append(assignment.office.key)
    for membership in _running_after(GroupMembership, member, on):
        end_membership(membership, _last_day(membership, on), ended_by=ended_by)
        ended['groups'].append(membership.group.name)
    clear_cache()
    return ended
