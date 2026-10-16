from community.core.db import TimestampMixin, UTCDateTime
from community.extensions import db

# Dryer programs of the old Google Form ("Trocknen Modus"), in its order.
DRY_PROGRAMS = (
    'Extra Trocken',
    'Schranktrocken +',
    'Bügeltrocken K',
    'Sehr Trocken',
    'Schranktrocken',
    'Bügeltrocken P',
    'Warm 20 min',
    'Kalt 20 min',
)


class PriceTier:
    """Members (any rank) pay the member price; residents without membership the resident price."""

    MEMBER = 'member'
    RESIDENT = 'resident'
    LABELS = {MEMBER: 'Mitglied', RESIDENT: 'Hausbewohner (kein Mitglied)'}


class PriceKind:
    WASH = 'wash'
    DRY = 'dry'
    LABELS = {WASH: 'Waschen', DRY: 'Trocknen'}


class MachineStatus:
    ACTIVE = 'active'
    DEFECT = 'defect'
    RETIRED = 'retired'
    LABELS = {ACTIVE: 'in Ordnung', DEFECT: 'defekt', RETIRED: 'außer Betrieb'}


class LaundryMachine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    kind = db.Column(db.String(10), nullable=False)  # PriceKind: wash or dry
    status = db.Column(db.String(10), nullable=False, default=MachineStatus.ACTIVE)
    sort_order = db.Column(db.Integer, nullable=False, default=0)


class LaundryPrice(TimestampMixin, db.Model):
    """Price of one wash or one dryer run for a tier, valid from a date (history kept)."""

    __table_args__ = (db.UniqueConstraint('tier', 'kind', 'valid_from', name='tier_kind_from'),)

    id = db.Column(db.Integer, primary_key=True)
    tier = db.Column(db.String(10), nullable=False)
    kind = db.Column(db.String(10), nullable=False)
    price_cents = db.Column(db.Integer, nullable=False)
    valid_from = db.Column(db.Date, nullable=False)
    set_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))


class UseSource:
    QR = 'qr'
    MANUAL = 'manual'
    IMPORT = 'import'


class LaundryUse(TimestampMixin, db.Model):
    """One entry of the Waschliste. Prices are copied in when it is saved, so later price
    changes never change old entries."""

    __table_args__ = (
        db.CheckConstraint('washes >= 0 AND washes <= 2', name='washes_range'),
        db.CheckConstraint('washes > 0 OR dried', name='something_used'),
    )

    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('member.id'), nullable=False, index=True)
    used_at = db.Column(UTCDateTime, nullable=False, index=True)
    washes = db.Column(db.Integer, nullable=False, default=0)  # 0, 1 ("eine") or 2 ("beide")
    dried = db.Column(db.Boolean, nullable=False, default=False)
    dry_program = db.Column(db.String(40))
    container = db.Column(db.String(80))  # "Transport": which tub or box, e.g. "blaue Wanne"
    tier = db.Column(db.String(10), nullable=False)
    wash_price_cents = db.Column(db.Integer, nullable=False)
    dry_price_cents = db.Column(db.Integer, nullable=False)
    source = db.Column(db.String(10), nullable=False, default=UseSource.QR)
    submission_id = db.Column(db.String(64), unique=True, nullable=False)
    recorded_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    voided_at = db.Column(UTCDateTime)
    voided_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    void_reason = db.Column(db.String(200))

    member = db.relationship('Member', lazy='joined')

    @property
    def total_cents(self):
        return self.washes * self.wash_price_cents + (self.dry_price_cents if self.dried else 0)

    @property
    def is_void(self):
        return self.voided_at is not None
