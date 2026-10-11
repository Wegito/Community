from community.core.db import TimestampMixin, UTCDateTime
from community.extensions import db


class Resource(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)  # z.B. "Saal", "Beamer"
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())


class BookingStatus:
    ACTIVE = 'active'
    CANCELLED = 'cancelled'


class Booking(TimestampMixin, db.Model):
    __table_args__ = (db.CheckConstraint('"end" > start', name='end_after_start'),)

    id = db.Column(db.Integer, primary_key=True)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    start = db.Column(UTCDateTime, nullable=False)
    end = db.Column(UTCDateTime, nullable=False)
    status = db.Column(db.String(20), nullable=False, default=BookingStatus.ACTIVE, server_default=BookingStatus.ACTIVE)

    resource = db.relationship('Resource', lazy='joined')
    user = db.relationship('User', lazy='joined')

    def can_cancel(self, user):
        return self.status == BookingStatus.ACTIVE and (user.id == self.user_id or user.role == 'admin')
