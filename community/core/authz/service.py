"""Who may do what.

active_roles(member) = roles from the current rank, active Ämter, active groups and
directly granted (custom/system) roles. A member whose status is ENDED has only the
former-member role. Permissions are the union of the roles' grants (no deny rules).
The result is computed once per request and cached in flask.g.
"""

import fnmatch
from datetime import timedelta
from pathlib import Path

import yaml
from flask import g, has_request_context
from flask_login import current_user
from sqlalchemy import or_, select

from community.core.identity.models import MemberStatus
from community.core.platform import audit
from community.core.time import today_local, utcnow
from community.extensions import db

from .models import FORMER_MEMBER, MemberRole, Permission, Role, RolePermission
from .registry import PERMISSIONS

SEED_FILE = Path(__file__).resolve().parent.parent.parent / 'seed' / 'authz.yaml'

# Functions (member, day) -> set of role ids. core.org adds rank, office and group sources.
ROLE_SOURCES = []


def role_source(fn):
    ROLE_SOURCES.append(fn)
    return fn


def active_on(model, day):
    """SQL condition: model.valid_from <= day <= model.valid_to (valid_to NULL = open-ended)."""
    return (model.valid_from <= day) & or_(model.valid_to.is_(None), model.valid_to >= day)


def yesterday():
    """The last day of something that ends now: valid_to is inclusive, so "today" would keep it until midnight."""
    return today_local() - timedelta(days=1)


def end_period(row, last_day):
    """End a dated row (rank, Amt, group, direct role) after `last_day`. Commits nothing.

    A row that starts after `last_day` never takes effect and is deleted instead of shrinking to one day
    (decision 66). Returns 'ended', 'deleted', or None if it was over by then anyway.
    """
    if row.valid_from > last_day:
        db.session.delete(row)
        db.session.flush()
        return 'deleted'
    if row.valid_to is not None and row.valid_to <= last_day:
        return None
    row.valid_to = last_day
    return 'ended'


@role_source
def _direct_roles(member, day):
    query = select(MemberRole.role_id).where(MemberRole.member_id == member.id, active_on(MemberRole, day))
    return set(db.session.scalars(query))


def role_by_key(key):
    return db.session.scalar(select(Role).where(Role.key == key))


def active_role_ids(member, day=None):
    day = day or today_local()
    if member.status == MemberStatus.ENDED:
        role = role_by_key(FORMER_MEMBER)
        return {role.id} if role else set()
    ids = set()
    for source in ROLE_SOURCES:
        ids |= source(member, day)
    return ids


def active_roles(member, day=None):
    ids = active_role_ids(member, day)
    if not ids:
        return []
    return db.session.scalars(select(Role).where(Role.id.in_(ids)).order_by(Role.kind, Role.label)).all()


def role_permissions(role_ids):
    """The permission codes granted to any of these roles."""
    if not role_ids:
        return frozenset()
    codes = db.session.scalars(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id.in_(role_ids))
    )
    return frozenset(codes)


def permissions_for(member, day=None):
    return role_permissions(active_role_ids(member, day))


def current_permissions():
    if not has_request_context() or not current_user.is_authenticated:
        return frozenset()
    if '_permissions' not in g:
        g._permissions = permissions_for(current_user.member)
    return g._permissions


def can(code):
    """True if the logged-in user holds `code` today."""
    return code in current_permissions()


def clear_cache():
    if has_request_context():
        g.pop('_permissions', None)


def holders_of(code, day=None):
    """Members who hold permission `code` (used for notifications such as "tell the Kassenwart")."""
    from community.core.identity.models import Member

    members = db.session.scalars(select(Member).where(Member.status == MemberStatus.ACTIVE)).all()
    return [m for m in members if code in permissions_for(m, day)]


def grant_member_role(member, role_key, *, valid_from=None, granted_by=None, reason=None):
    """Give a member a custom or system role. Commits nothing."""
    role = role_by_key(role_key)
    if role is None:
        raise LookupError(f'Unknown role {role_key!r}')
    assignment = MemberRole(
        member_id=member.id,
        role_id=role.id,
        valid_from=valid_from or today_local(),
        granted_by_user_id=getattr(granted_by, 'id', None),
        reason=reason,
    )
    db.session.add(assignment)
    clear_cache()
    return assignment


def _member_roles_after(member, on, role=None):
    query = select(MemberRole).where(
        MemberRole.member_id == member.id, or_(MemberRole.valid_to.is_(None), MemberRole.valid_to > on)
    )
    if role is not None:
        query = query.where(MemberRole.role_id == role.id)
    return db.session.scalars(query.order_by(MemberRole.valid_from, MemberRole.id)).all()


def end_member_role(member, role_key, *, on=None):
    """End a direct role after `on`, its last day (default: yesterday, so it ends at once). Commits nothing.

    Assignments that start after `on` are deleted (end_period). Returns the assignments ended or deleted.
    """
    role = role_by_key(role_key)
    on = on or yesterday()
    rows = _member_roles_after(member, on, role)
    for row in rows:
        end_period(row, on)
    clear_cache()
    return rows


def end_all_member_roles(member, on):
    """End every direct role of `member` still running after `on` (Austritt). Commits nothing.

    Each ends on `on`; one that starts later is deleted (end_period). Returns the role keys.
    """
    ended = []
    for row in _member_roles_after(member, on):
        ended.append(row.role.key)
        end_period(row, on)
    clear_cache()
    return sorted(ended)


def sync_permissions():
    """Copy declared permission codes into the database. Returns (added, updated, stale codes)."""
    now = utcnow()
    existing = {p.code: p for p in db.session.scalars(select(Permission))}
    added = updated = 0
    for code, definition in PERMISSIONS.items():
        row = existing.get(code)
        if row is None:
            db.session.add(
                Permission(
                    code=code,
                    module=definition.module,
                    description=definition.description,
                    is_sensitive=definition.sensitive,
                    last_seen_at=now,
                )
            )
            added += 1
        else:
            if (row.description, row.module, row.is_sensitive) != (
                definition.description,
                definition.module,
                definition.sensitive,
            ):
                row.description, row.module, row.is_sensitive = (
                    definition.description,
                    definition.module,
                    definition.sensitive,
                )
                updated += 1
            row.last_seen_at = now
    db.session.commit()
    stale = sorted(set(existing) - set(PERMISSIONS))
    return added, updated, stale


def load_seed(path=None):
    with open(path or SEED_FILE, encoding='utf-8') as fh:
        data = yaml.safe_load(fh) or {}
    return data.get('grants', {})


def expand_codes(entries, all_codes):
    """Codes of one seed entry list. Plain codes stay as they are; patterns select from `all_codes`:
    '*' or 'tasks.*' adds every matching code, '!finance.*' removes matches again (wherever it stands).
    """
    include, exclude = [], []
    for entry in entries or []:
        (exclude if entry.startswith('!') else include).append(entry.lstrip('!'))
    codes = []
    for entry in include:
        if any(char in entry for char in '*?['):
            codes.extend(sorted(code for code in all_codes if fnmatch.fnmatchcase(code, entry)))
        else:
            codes.append(entry)
    seen = set()
    return [
        code
        for code in codes
        if not any(fnmatch.fnmatchcase(code, pattern) for pattern in exclude) and not (code in seen or seen.add(code))
    ]


def seed_grants(path=None, *, granted_by=None, only_codes=None):
    """Add the default grants from seed/authz.yaml (or `path`). Never removes grants. Commits.

    only_codes: add only grants of these codes (flask setup: codes that are new in the database).
    Each role that gets grants gets one audit entry naming the codes, the sensitive ones separately.
    Returns a dict with counts and the role keys / codes that were skipped because
    they do not exist (yet).
    """
    grants = load_seed(path)
    roles = {r.key: r for r in db.session.scalars(select(Role))}
    permissions = {p.code: p for p in db.session.scalars(select(Permission))}
    have = set(db.session.execute(select(RolePermission.role_id, RolePermission.permission_id)).tuples())
    added = 0
    missing_roles, missing_codes = set(), set()
    for role_key, codes in grants.items():
        role = roles.get(role_key)
        if role is None:
            missing_roles.add(role_key)
            continue
        new_codes = []
        for code in expand_codes(codes, permissions):
            if only_codes is not None and code not in only_codes:
                continue
            permission = permissions.get(code)
            if permission is None:
                missing_codes.add(code)
                continue
            if (role.id, permission.id) not in have:
                db.session.add(
                    RolePermission(
                        role_id=role.id,
                        permission_id=permission.id,
                        granted_by_user_id=getattr(granted_by, 'id', None),
                    )
                )
                have.add((role.id, permission.id))
                new_codes.append(code)
        if new_codes:
            added += len(new_codes)
            diff = {'role': role_key, 'codes': sorted(new_codes), 'source': Path(path or SEED_FILE).name}
            sensitive = sorted(code for code in new_codes if permissions[code].is_sensitive)
            if sensitive:
                diff['sensitive'] = sensitive
            audit.record('authz.role_permission.grant', role, actor=granted_by, diff=diff)
    db.session.commit()
    return {'added': added, 'missing_roles': sorted(missing_roles), 'missing_codes': sorted(missing_codes)}
