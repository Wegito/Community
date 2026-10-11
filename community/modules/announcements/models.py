from community.core.db import UTCDateTime
from community.core.time import utcnow
from community.extensions import db


class Announcement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(UTCDateTime, default=utcnow)
