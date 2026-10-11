from community.core.db import UTCDateTime
from community.core.time import utcnow
from community.extensions import db


class Resource(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)  # z.B. "Saal", "Beamer"


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    start = db.Column(UTCDateTime, nullable=False)
    end = db.Column(UTCDateTime, nullable=False)
    created_at = db.Column(UTCDateTime, default=utcnow)
