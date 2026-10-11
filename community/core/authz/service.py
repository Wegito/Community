"""Who may do what.

active_roles(member) = roles from the current rank, active Ämter, active groups and
directly granted (custom/system) roles. A member whose status is ENDED has only the
former-member role. Permissions are the union of the roles' grants (no deny rules).
The result is computed once per request and cached in flask.g.
"""

from pathlib import Path

import yaml
from flask import g, has_request_context
from flask_login import current_user
from sqlalchemy import or_, select

from community.core.identity.models import MemberStatus
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


def permissions_for(member, day=None):
    ids = active_role_ids(member, day)
    if not ids:
        return frozenset()
    codes = db.session.scalars(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id.in_(ids))
    )
    return frozenset(codes)


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


def end_member_role(member, role_key, *, on=None):
    """End all open assignments of a direct role (valid_to inclusive). Commits nothing."""
    role = role_by_key(role_key)
    on = on or today_local()
    rows = db.session.scalars(
        select(MemberRole).where(
            MemberRole.member_id == member.id, MemberRole.role_id == role.id, active_on(MemberRole, on)
        )
    ).all()
    for row in rows:
        row.valid_to = on
    clear_cache()
    return rows


def end_all_member_roles(member, on):
    """End every direct role of `member` still running after `on` (Austritt). Commits nothing.

    Each ends on `on`, or on its first day if it starts later. Returns the role keys ended.
    """
    rows = db.session.scalars(
        select(MemberRole).where(
            MemberRole.member_id == member.id, or_(MemberRole.valid_to.is_(None), MemberRole.valid_to > on)
        )
    ).all()
    ended = []
    for row in rows:
        last_day = max(on, row.valid_from)
        if row.valid_to is not None and row.valid_to <= last_day:
            continue
        row.valid_to = last_day
        ended.append(row.role.key)
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


def seed_grants(path=None, *, granted_by=None):
    """Add the default grants from seed/authz.yaml. Never removes grants.

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
        for code in codes or []:
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
                added += 1
    db.session.commit()
    return {'added': added, 'missing_roles': sorted(missing_roles), 'missing_codes': sorted(missing_codes)}
