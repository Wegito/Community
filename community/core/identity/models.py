from datetime import datetime, timedelta

from flask_login import UserMixin
from sqlalchemy import case, literal, or_, update
from werkzeug.security import check_password_hash, generate_password_hash

from community.core.db import UTCDateTime
from community.core.time import utcnow
from community.extensions import db

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256


class Role:
    RESIDENT = 'resident'
    ADMIN = 'admin'


def normalize_email(value):
    """Emails are stored and compared trimmed and lowercase."""
    return value.strip().lower() if value else value


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default=Role.RESIDENT, nullable=False)
    created_at = db.Column(UTCDateTime, default=utcnow)
    failed_logins = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    locked_until = db.Column(UTCDateTime)

    def set_password(self, pw: str):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
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
    def reset_login_attempts(cls, user_id):
        db.session.execute(
            update(cls)
            .where(cls.id == user_id)
            .values(failed_logins=0, locked_until=None)
            .execution_options(synchronize_session=False)
        )
        db.session.commit()
