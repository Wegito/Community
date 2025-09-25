from datetime import datetime, date
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


DB = SQLAlchemy()


class Role:
    RESIDENT = 'resident'
    ADMIN = 'admin'


class User(UserMixin, DB.Model):
    id = DB.Column(DB.Integer, primary_key=True)
    email = DB.Column(DB.String(255), unique=True, nullable=False)
    name = DB.Column(DB.String(120), nullable=False)
    password_hash = DB.Column(DB.String(255), nullable=False)
    role = DB.Column(DB.String(20), default=Role.RESIDENT, nullable=False)
    created_at = DB.Column(DB.DateTime, default=datetime.utcnow)


    def set_password(self, pw: str):
        self.password_hash = generate_password_hash(pw)


    def check_password(self, pw: str) -> bool:
        return check_password_hash(self.password_hash, pw)


class Announcement(DB.Model):
    id = DB.Column(DB.Integer, primary_key=True)
    title = DB.Column(DB.String(200), nullable=False)
    body = DB.Column(DB.Text, nullable=False)
    author_id = DB.Column(DB.Integer, DB.ForeignKey('user.id'))
    created_at = DB.Column(DB.DateTime, default=datetime.utcnow)


class Chore(DB.Model):
    id = DB.Column(DB.Integer, primary_key=True)
    title = DB.Column(DB.String(200), nullable=False)
    assigned_to_id = DB.Column(DB.Integer, DB.ForeignKey('user.id'))
    due_date = DB.Column(DB.Date)
    is_done = DB.Column(DB.Boolean, default=False)


class Resource(DB.Model):
    id = DB.Column(DB.Integer, primary_key=True)
    name = DB.Column(DB.String(120), unique=True, nullable=False) # z.B. "Saal", "Beamer"


class Booking(DB.Model):
    id = DB.Column(DB.Integer, primary_key=True)
    resource_id = DB.Column(DB.Integer, DB.ForeignKey('resource.id'), nullable=False)
    user_id = DB.Column(DB.Integer, DB.ForeignKey('user.id'), nullable=False)
    start = DB.Column(DB.DateTime, nullable=False)
    end = DB.Column(DB.DateTime, nullable=False)
    created_at = DB.Column(DB.DateTime, default=datetime.utcnow)