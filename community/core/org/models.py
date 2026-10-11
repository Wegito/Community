from community.core.db import TimestampMixin, UTCDateTime
from community.extensions import db


class SemesterKind:
    WINTER = 'WS'
    SUMMER = 'SS'


class SemesterStatus:
    PLANNING = 'planning'
    ACTIVE = 'active'
    CLOSED = 'closed'
    LABELS = {PLANNING: 'In Planung', ACTIVE: 'Laufend', CLOSED: 'Abgeschlossen'}


class Semester(db.Model):
    __table_args__ = (db.CheckConstraint('starts_on < ends_on', name='starts_before_end'),)

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False)  # "WS26/27", "SS27"
    kind = db.Column(db.String(2), nullable=False)
    starts_on = db.Column(db.Date, nullable=False)
    ends_on = db.Column(db.Date, nullable=False)  # inclusive
    status = db.Column(db.String(10), nullable=False, default=SemesterStatus.PLANNING)


class Rank(db.Model):
    """Spefux, Fux, Bursch, Alter Herr. Labels are data and can be renamed."""

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(40), unique=True, nullable=False)
    label = db.Column(db.String(80), nullable=False)
    abbreviation = db.Column(db.String(10), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'), unique=True, nullable=False)
    is_aktivitas = db.Column(db.Boolean, nullable=False, default=False)
    is_alumni = db.Column(db.Boolean, nullable=False, default=False)
    archived_at = db.Column(UTCDateTime)

    role = db.relationship('Role', lazy='joined')


class RankHistory(TimestampMixin, db.Model):
    """One row per period in a rank. Exactly one open row (valid_to NULL) per member."""

    __table_args__ = (
        db.Index(
            'uq_rank_history_open_member',
            'member_id',
            unique=True,
            sqlite_where=db.text('valid_to IS NULL'),
            postgresql_where=db.text('valid_to IS NULL'),
        ),
        db.CheckConstraint('valid_to IS NULL OR valid_to >= valid_from', name='valid_range'),
    )

    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('member.id'), nullable=False, index=True)
    rank_id = db.Column(db.Integer, db.ForeignKey('rank.id'), nullable=False)
    valid_from = db.Column(db.Date, nullable=False)
    valid_to = db.Column(db.Date)  # inclusive; NULL = current
    convent_ref = db.Column(db.String(80))  # e.g. "CC 12.10.2026, TOP 4"
    note = db.Column(db.String(200))
    changed_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))

    rank = db.relationship('Rank', lazy='joined')
    changed_by = db.relationship('User')
