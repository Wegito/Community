from community.core.db import UTCDateTime
from community.core.time import utcnow
from community.extensions import db


class DrinkSource:
    SHEET = 'sheet'  # the Bierwart's Strichliste
    API = 'api'  # a device such as the bar tablet
    LABELS = {SHEET: 'Strichliste', API: 'Tablet'}


class DrinkProduct(db.Model):
    """Something the Bierwart sells: a beer, a soft drink, a Schnaps. Names and prices are data."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    counts_as_beer = db.Column(db.Boolean, nullable=False, default=False)  # for the Bierkönig
    volume_ml = db.Column(db.Integer)  # one unit, for the litres on the Fass-Barometer
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    archived_at = db.Column(UTCDateTime)

    prices = db.relationship('DrinkPrice', order_by='DrinkPrice.valid_from.desc()', lazy='selectin')

    @property
    def is_active(self):
        return self.archived_at is None


class DrinkPrice(db.Model):
    """The price of a product from a day on; a new price is a new row (old tallies keep theirs)."""

    __table_args__ = (
        db.UniqueConstraint('product_id', 'valid_from', name='one_price_per_day'),
        db.CheckConstraint('price_cents >= 0', name='not_negative'),
    )

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('drink_product.id'), nullable=False, index=True)
    price_cents = db.Column(db.Integer, nullable=False)
    valid_from = db.Column(db.Date, nullable=False)


class DrinkConsumption(db.Model):
    """Striche: `quantity` units of a product for a member. Never edited or deleted; a mistake gets a
    correction row with the negative quantity (`corrects_id`). The unit price is copied in (D11)."""

    __table_args__ = (
        db.CheckConstraint('quantity != 0', name='not_zero'),
        db.CheckConstraint('(quantity < 0) = (corrects_id IS NOT NULL)', name='negative_iff_correction'),
    )

    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('member.id'), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey('drink_product.id'), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price_cents = db.Column(db.Integer, nullable=False)
    occurred_at = db.Column(UTCDateTime, nullable=False, index=True)
    source = db.Column(db.String(10), nullable=False)
    # Same key twice = the same Striche (double click, a tablet sending again): stored once.
    idempotency_key = db.Column(db.String(120), unique=True)
    corrects_id = db.Column(db.Integer, db.ForeignKey('drink_consumption.id'), unique=True)
    recorded_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    api_client_id = db.Column(db.Integer, db.ForeignKey('api_client.id'))
    created_at = db.Column(UTCDateTime, nullable=False, default=utcnow)
    note = db.Column(db.String(200))

    member = db.relationship('Member', lazy='joined')
    product = db.relationship('DrinkProduct', lazy='joined')
    recorded_by = db.relationship('User')
    corrects = db.relationship('DrinkConsumption', remote_side=[id], foreign_keys=[corrects_id])
    corrected_by = db.relationship('DrinkConsumption', foreign_keys=[corrects_id], uselist=False, viewonly=True)

    @property
    def total_cents(self):
        return self.quantity * self.unit_price_cents
