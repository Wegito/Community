from community.core.db import TimestampMixin, UTCDateTime
from community.core.time import utcnow
from community.extensions import db


class RoleKind:
    RANK = 'rank'
    OFFICE = 'office'
    GROUP = 'group'
    CUSTOM = 'custom'
    SYSTEM = 'system'


SYSADMIN = 'custom.sysadmin'
FORMER_MEMBER = 'system.former_member'


class Permission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), unique=True, nullable=False)
    module = db.Column(db.String(40), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    is_sensitive = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    last_seen_at = db.Column(UTCDateTime)


class Role(db.Model):
    """Anything that grants rights: a rank, an Amt, a group, a custom or a system role."""

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False)  # e.g. rank.bursch, office.kassenwart
    label = db.Column(db.String(120), nullable=False)
    kind = db.Column(db.String(10), nullable=False)

    permissions = db.relationship('Permission', secondary='role_permission', lazy='selectin', viewonly=True)


class RolePermission(db.Model):
    role_id = db.Column(db.Integer, db.ForeignKey('role.id', ondelete='CASCADE'), primary_key=True)
    permission_id = db.Column(db.Integer, db.ForeignKey('permission.id', ondelete='CASCADE'), primary_key=True)
    granted_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    granted_at = db.Column(UTCDateTime, default=utcnow)


class MemberRole(TimestampMixin, db.Model):
    """Custom and system roles given to a member directly (e.g. sysadmin). Ranks, Ämter and
    groups grant their roles through their own assignments instead."""

    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('member.id'), nullable=False, index=True)
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'), nullable=False)
    valid_from = db.Column(db.Date, nullable=False)
    valid_to = db.Column(db.Date)  # inclusive; NULL = open-ended
    granted_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    reason = db.Column(db.String(200))

    role = db.relationship('Role', lazy='joined')
