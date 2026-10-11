from datetime import datetime, timedelta

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from community.core.db import UTCDateTime
from community.core.time import utcnow
from community.extensions import db


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

    def is_locked(self, now: datetime) -> bool:
        return self.locked_until is not None and self.locked_until > now

    def register_failed_login(self, now: datetime, max_attempts: int, lockout: timedelta):
        """Count a failed login; lock the account after max_attempts failures in a row."""
        self.failed_logins = (self.failed_logins or 0) + 1
        if self.failed_logins >= max_attempts:
            self.locked_until = now + lockout
            self.failed_logins = 0

    def register_successful_login(self):
        self.failed_logins = 0
        self.locked_until = None
