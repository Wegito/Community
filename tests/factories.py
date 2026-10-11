"""Small helpers to create test data directly in the database."""

import itertools
from datetime import date

from sqlalchemy import select

from community.core.authz.models import SYSADMIN, MemberRole, Permission, Role, RoleKind, RolePermission
from community.core.authz.service import grant_member_role, load_seed
from community.core.identity.models import split_name
from community.core.identity.services import create_member, create_user
from community.extensions import db

DEFAULT_PASSWORD = 'correct horse battery'
_role_numbers = itertools.count(1)


def seed_codes(role_key):
    """The permission codes a role gets from seed/authz.yaml (declared codes only)."""
    declared = set(db.session.scalars(select(Permission.code)))
    return [code for code in load_seed()[role_key] if code in declared]


def give_permissions(member, *codes):
    """Give a member exactly these extra permissions through a throwaway custom role."""
    key = f'custom.test{next(_role_numbers)}'
    role = Role(key=key, label=key, kind=RoleKind.CUSTOM)
    db.session.add(role)
    db.session.flush()
    for code in codes:
        permission = db.session.scalar(select(Permission).where(Permission.code == code))
        assert permission is not None, f'unknown permission {code}'
        db.session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    db.session.add(MemberRole(member_id=member.id, role_id=role.id, valid_from=date(2000, 1, 1)))
    db.session.commit()
    return role


def make_member(name='Max Mustermann', email=None, **fields):
    first, last = split_name(name)
    member = create_member(first, last, email=email, **fields)
    db.session.commit()
    return member


def make_user(email='max@example.org', name='Max Mustermann', password=DEFAULT_PASSWORD, perms=None):
    """A user with a login. By default it has the permissions of a Bursch (from the seed)."""
    first, last = split_name(name)
    member = create_member(first, last, email=email)
    user = create_user(member, email, password=password)
    db.session.commit()
    codes = seed_codes('rank.bursch') if perms is None else perms
    if codes:
        give_permissions(member, *codes)
    return user


def make_manager(email='chef@example.org', name='Chef Senior', **kwargs):
    """A Bursch who also manages announcements, tasks and bookings (like a Senior/Consenior)."""
    extra = ['announcements.create', 'announcements.manage_any', 'tasks.manage', 'bookings.manage_any']
    return make_user(email=email, name=name, perms=seed_codes('rank.bursch') + extra, **kwargs)


def make_admin(email='admin@example.org', name='Admin Istrator', password=DEFAULT_PASSWORD):
    """A technical admin: only the custom.sysadmin role, no rank."""
    user = make_user(email=email, name=name, password=password, perms=[])
    grant_member_role(user.member, SYSADMIN)
    db.session.commit()
    return user
