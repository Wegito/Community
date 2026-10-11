from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from community.core.db import UTCDateTime
from community.core.time import utcnow
from community.extensions import db


class Role:
    RESIDENT = 'resident'
    ADMIN = 'admin'


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default=Role.RESIDENT, nullable=False)
    created_at = db.Column(UTCDateTime, default=utcnow)

    def set_password(self, pw: str):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
        return check_password_hash(self.password_hash, pw)
