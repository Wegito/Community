import uuid
from datetime import datetime, timedelta

from flask_login import UserMixin
from sqlalchemy import case, literal, or_, update
from werkzeug.security import check_password_hash, generate_password_hash

from community.core.db import TimestampMixin, UTCDateTime
from community.extensions import db

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256


def normalize_email(value):
    """Emails are stored and compared trimmed and lowercase."""
    return value.strip().lower() if value else value


class MemberStatus:
    ACTIVE = 'active'
    ENDED = 'ended'


class ExitReason:
    """Why a member left (member.ended_reason)."""

    AUSTRITT = 'austritt'
    AUSSCHLUSS = 'ausschluss'
    VERSTORBEN = 'verstorben'
    SONSTIGES = 'sonstiges'
    LABELS = {AUSTRITT: 'Austritt', AUSSCHLUSS: 'Ausschluss', VERSTORBEN: 'Verstorben', SONSTIGES: 'Sonstiges'}


class Member(TimestampMixin, db.Model):
    """A person in the club. Exists with or without a login (e.g. an imported Alter Herr)."""

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False, default='', server_default='')
    biername = db.Column(db.String(80))
    email = db.Column(db.String(255), unique=True)  # contact address; the login address is on User
    phone = db.Column(db.String(40))
    address = db.Column(db.Text)
    status = db.Column(db.String(10), nullable=False, default=MemberStatus.ACTIVE, server_default=MemberStatus.ACTIVE)
    joined_on = db.Column(db.Date)
    ended_on = db.Column(db.Date)
    ended_reason = db.Column(db.String(40))
    payment_ref = db.Column(db.String(20), unique=True)  # Verwendungszweck, e.g. "M-0042"
    show_phone = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    show_address = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    pseudonymized_at = db.Column(UTCDateTime)

    user = db.relationship('User', back_populates='member', uselist=False)

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'.strip()

    @property
    def display_name(self):
        """Name shown in lists: "Max Mustermann" or "Max Mustermann (Biername)"."""
        return f'{self.full_name} ({self.biername})' if self.biername else self.full_name

    @property
    def is_active_member(self):
        return self.status == MemberStatus.ACTIVE

    @property
    def ended_reason_label(self):
        return ExitReason.LABELS.get(self.ended_reason, self.ended_reason)

    def __repr__(self):
        return f'<Member {self.id} {self.full_name}>'


def assign_payment_ref(member):
    """Set the transfer reference from the id once the member has one (call after flush)."""
    if member.payment_ref is None and member.id is not None:
        member.payment_ref = f'M-{member.id:04d}'


def split_name(name):
    """Split a free-text name into first and last name at the first space."""
    parts = (name or '').strip().split(None, 1)
    if not parts:
        return '', ''
    return parts[0], parts[1] if len(parts) > 1 else ''


class User(UserMixin, TimestampMixin, db.Model):
    """A login account. Every user belongs to exactly one member."""

    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('member.id'), unique=True, nullable=False)
    login_email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255))  # NULL = no password set (e.g. Google-only later)
    is_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    session_version = db.Column(db.Integer, nullable=False, default=1, server_default='1')
    failed_logins = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    locked_until = db.Column(UTCDateTime)
    last_login_at = db.Column(UTCDateTime)

    member = db.relationship('Member', back_populates='user', lazy='joined')

    def get_id(self):
        # The session and the remember-me cookie store "id:version". Raising session_version
        # ("log out everywhere", password reset) makes every stored login invalid.
        return f'{self.id}:{self.session_version}'

    @property
    def is_active(self):
        """Flask-Login refuses logins and sessions of disabled accounts."""
        return bool(self.is_enabled)

    @property
    def display_name(self):
        return self.member.display_name

    def set_password(self, pw: str):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, pw)

    @classmethod
    def reserve_login_attempt(cls, user_id, now: datetime, max_attempts: int, lockout: timedelta) -> bool:
        """Count one login attempt before the password is checked. False = account is locked.

        A single UPDATE counts and locks at once, so parallel requests cannot get more than
        max_attempts password checks per lockout window. The attempt that reaches the limit
        sets the lock and is still checked.
        """
        reaches_limit = cls.failed_logins + 1 >= max_attempts
        result = db.session.execute(
            update(cls)
            .where(cls.id == user_id, or_(cls.locked_until.is_(None), cls.locked_until <= now))
            .values(
                failed_logins=case((reaches_limit, 0), else_=cls.failed_logins + 1),
                locked_until=case((reaches_limit, literal(now + lockout, UTCDateTime)), else_=None),
            )
            .execution_options(synchronize_session=False)
        )
        db.session.commit()
        return result.rowcount == 1

    @classmethod
    def record_successful_login(cls, user_id, now: datetime):
        db.session.execute(
            update(cls)
            .where(cls.id == user_id)
            .values(failed_logins=0, locked_until=None, last_login_at=literal(now, UTCDateTime))
            .execution_options(synchronize_session=False)
        )
        db.session.commit()
