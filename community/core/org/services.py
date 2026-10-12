"""Semesters and ranks: the rules (who may change what, which dates are valid)."""

from datetime import date, timedelta

from sqlalchemy import or_, select

from community.core.authz.service import active_on, clear_cache, end_period, role_source, yesterday
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


def latest_rank_entry(member):
    """The member's last rank period: the open one, or the one an Austritt closed."""
    return db.session.scalar(
        select(RankHistory).where(RankHistory.member_id == member.id).order_by(RankHistory.valid_from.desc()).limit(1)
    )


def first_free_rank_day(member, day=None):
    """`day` (default today), or the day after the last rank ended if that is later (Wiederaufnahme)."""
    day = day or today_local()
    latest = latest_rank_entry(member)
    if latest is not None and latest.valid_to is not None and latest.valid_to >= day:
        return latest.valid_to + timedelta(days=1)
    return day


def change_rank(member, rank, valid_from, *, changed_by=None, convent_ref=None, note=None, **hook_options):
    """Put a member into `rank` from `valid_from` on. Commits nothing.

    changed_by=None means the server operator (CLI); otherwise nobody may change their own rank.
    Rank periods never overlap: the new one starts after the current one began, or after the last one
    ended (after a Wiederaufnahme). A rank that starts today or later can be replaced from the same day,
    e.g. a wrong entry (decision 66).
    """
    from community.core.identity.models import MemberStatus

    if changed_by is not None and changed_by.member_id == member.id:
        raise OrgError('Den eigenen Rang kann man nicht selbst ändern.')
    if member.status != MemberStatus.ACTIVE:
        raise OrgError(f'{member.full_name} ist kein aktives Mitglied.')
    if rank.archived_at is not None:
        raise OrgError('Dieser Rang wird nicht mehr verwendet.')
    latest = latest_rank_entry(member)
    if latest is not None and latest.valid_to is not None and valid_from <= latest.valid_to:
        raise OrgError(f'Das Datum muss nach dem Ende des letzten Rangs ({latest.valid_to:%d.%m.%Y}) liegen.')
    open_entry = latest if latest is not None and latest.valid_to is None else None
    previous, replaced = open_entry, None
    if open_entry is not None:
        if open_entry.rank_id == rank.id:
            raise OrgError(f'{member.full_name} ist bereits {rank.label}.')
        if valid_from == open_entry.valid_from and valid_from >= today_local():
            replaced = open_entry
            previous = _replace_rank_entry(open_entry)
        elif valid_from <= open_entry.valid_from:
            raise OrgError(
                f'Das Datum muss nach dem Beginn des aktuellen Rangs ({open_entry.valid_from:%d.%m.%Y}) liegen.'
            )
        else:
            open_entry.valid_to = valid_from - timedelta(days=1)
            db.session.flush()
    if previous is not None and previous.rank_id == rank.id:
        previous.valid_to = None  # the wrong entry is gone; the previous period simply goes on
        entry = previous
    else:
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
    diff = {
        'from': previous.rank.key if previous is not None else None,
        'to': rank.key,
        'valid_from': valid_from,
        'convent_ref': convent_ref,
    }
    if replaced is not None:
        diff['replaces'] = replaced.rank.key
    audit.record('org.rank.change', member, actor=changed_by, diff=diff)
    for hook in RANK_CHANGE_HOOKS:
        hook(member, rank, valid_from, changed_by, **hook_options)
    clear_cache()
    return entry


def members_without_rank():
    """Active members who never had a rank, e.g. accounts from before M1 or import rows without rank."""
    from community.core.identity.models import Member, MemberStatus

    return db.session.scalars(
        select(Member)
        .where(Member.status == MemberStatus.ACTIVE, Member.id.not_in(select(RankHistory.member_id)))
        .order_by(Member.first_name, Member.last_name)
    ).all()


def set_first_rank(member, rank, valid_from, *, changed_by):
    """Give a member who never had a rank their first one (bootstrap, decision 70). Commits nothing.

    Holders of members.import may do this, as the import sets initial ranks (decision 53); any later
    change, also after a Wiederaufnahme, is "Rang ändern" (members.rank.change). Audited by change_rank.
    """
    if latest_rank_entry(member) is not None:
        raise OrgError(f'{member.full_name} hat schon einen Rang; geändert wird er über „Rang ändern“.')
    if valid_from > today_local():
        raise OrgError('Der erste Rang kann nicht in der Zukunft beginnen.')
    # An Amt held before the rank was known is kommissarisch already and stays so.
    return change_rank(member, rank, valid_from, changed_by=changed_by, offices='keep_acting')


def _replace_rank_entry(entry):
    """Delete a rank entry that has not begun yet; returns the period before it, which ended the day before."""
    previous = db.session.scalar(
        select(RankHistory).where(
            RankHistory.member_id == entry.member_id, RankHistory.valid_to == entry.valid_from - timedelta(days=1)
        )
    )
    db.session.delete(entry)
    db.session.flush()
    return previous


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


def office_holders_from(office, day=None):
    """Assignments of `office` running on `day` (default today) or starting later (for the office page)."""
    day = day or today_local()
    return db.session.scalars(
        select(OfficeAssignment)
        .where(OfficeAssignment.office_id == office.id, overlaps_from(OfficeAssignment, day))
        .order_by(OfficeAssignment.valid_from)
    ).all()


def member_offices(member, day=None):
    day = day or today_local()
    return db.session.scalars(
        select(OfficeAssignment)
        .where(OfficeAssignment.member_id == member.id, active_on(OfficeAssignment, day))
        .order_by(OfficeAssignment.valid_from)
    ).all()


def member_offices_from(member, day=None):
    """Ämter of `member` running on `day` (default today) or starting later."""
    day = day or today_local()
    return db.session.scalars(
        select(OfficeAssignment)
        .where(OfficeAssignment.member_id == member.id, overlaps_from(OfficeAssignment, day))
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


def later_ineligible_rank(office, member, day):
    """The first rank entry recorded to start after `day` whose rank may not hold `office`, or None."""
    eligible = {r.id for r in office.eligible_ranks}
    entries = db.session.scalars(
        select(RankHistory)
        .where(RankHistory.member_id == member.id, RankHistory.valid_from > day)
        .order_by(RankHistory.valid_from)
    )
    return next((entry for entry in entries if entry.rank_id not in eligible), None)


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
    eligible = is_eligible(office, member, valid_from)
    later_rank = later_ineligible_rank(office, member, valid_from)
    if not is_acting and not (eligible and later_rank is None):
        labels = ', '.join(r.label for r in office.eligible_ranks) or 'niemand'
        rule = f'{office.label} können nur {labels} übernehmen. Ausnahme: kommissarisch (wird protokolliert).'
        if eligible:
            rule = f'{member.full_name} ist ab {later_rank.valid_from:%d.%m.%Y} {later_rank.rank.label}. {rule}'
        raise OrgError(rule)
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
            'eligible': eligible and later_rank is None,
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


def cancel_assignment(assignment, *, cancelled_by=None):
    """Austragen: delete an Amt that starts today or later, e.g. one given to the wrong member, so the
    right one can start the same day (decision 66). Commits nothing.

    A term that began before today stays in the history; it can only be ended.
    """
    if assignment.valid_from < today_local():
        raise OrgError(f'Die Amtszeit läuft seit {assignment.valid_from:%d.%m.%Y}. Bitte beenden statt austragen.')
    _delete_assignment(assignment, reason=None, actor=cancelled_by)


def _delete_assignment(assignment, *, reason, actor):
    audit.record(
        'org.office.cancel',
        assignment,
        actor=actor,
        diff={
            'office': assignment.office.key,
            'member_id': assignment.member_id,
            'valid_from': assignment.valid_from,
            'reason': reason,
        },
    )
    db.session.delete(assignment)
    db.session.flush()
    clear_cache()


def _close_assignment(assignment, last_day, *, reason, ended_by):
    """End an Amt after `last_day`; one that starts later never takes effect and is deleted."""
    if assignment.valid_from > last_day:
        _delete_assignment(assignment, reason=reason, actor=ended_by)
    else:
        end_assignment(assignment, last_day, reason=reason, ended_by=ended_by)


@role_source
def _office_roles(member, day):
    rows = db.session.scalars(
        select(Office.role_id)
        .join(OfficeAssignment, OfficeAssignment.office_id == Office.id)
        .where(OfficeAssignment.member_id == member.id, active_on(OfficeAssignment, day))
    )
    return set(rows)


def _offices_after_rank_change(member, rank, valid_from, changed_by, offices='close', **_):
    """Ämter the new rank may not hold, also those that start later: end them the day before (delete
    those that start on or after `valid_from`), or keep them as acting (audited)."""
    for assignment in member_offices_from(member, valid_from):
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
            _close_assignment(
                assignment, valid_from - timedelta(days=1), reason=f'Rangwechsel zu {rank.label}', ended_by=changed_by
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


def group_members_from(group, day=None):
    """Memberships of `group` running on `day` (default today) or starting later (for the group page)."""
    day = day or today_local()
    return db.session.scalars(
        select(GroupMembership)
        .where(GroupMembership.group_id == group.id, overlaps_from(GroupMembership, day))
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


def end_membership(membership, on=None, *, ended_by=None):
    """End a group membership after `on`, its last day (default: yesterday, so it ends at once).
    One that starts later is deleted (decision 66). Commits nothing."""
    on = on or yesterday()
    if membership.valid_to is not None and membership.valid_to <= on:
        return membership
    diff = {'group': membership.group.name, 'member_id': membership.member_id}
    if membership.valid_from > on:
        audit.record('org.group.cancel', membership, actor=ended_by, diff={**diff, 'valid_from': membership.valid_from})
    else:
        audit.record('org.group.remove', membership, actor=ended_by, diff={**diff, 'valid_to': on})
    end_period(membership, on)
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


def _running_after(model, member, on):
    return db.session.scalars(
        select(model)
        .where(model.member_id == member.id, or_(model.valid_to.is_(None), model.valid_to > on))
        .order_by(model.valid_from, model.id)
    ).all()


def end_member_periods(member, on, *, reason=None, ended_by=None):
    """End the rank, Ämter and group memberships of `member` still running after `on` (Austritt,
    roadmap §6.4), so successors are not blocked by max_holders. Commits nothing.

    Periods that start after `on` never take effect and are deleted (decision 66).
    Returns what was ended or deleted: {'rank': [rank keys], 'offices': [office keys], 'groups': [names]}.
    """
    ended = {'rank': [], 'offices': [], 'groups': []}
    for entry in _running_after(RankHistory, member, on):
        ended['rank'].append(entry.rank.key)
        end_period(entry, on)
    for assignment in _running_after(OfficeAssignment, member, on):
        ended['offices'].append(assignment.office.key)
        _close_assignment(assignment, on, reason=reason, ended_by=ended_by)
    for membership in _running_after(GroupMembership, member, on):
        ended['groups'].append(membership.group.name)
        end_membership(membership, on, ended_by=ended_by)
    clear_cache()
    return ended
