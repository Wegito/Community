from community.core.db import UTCDateTime
from community.core.org.services import add_semester_reference
from community.core.time import utcnow
from community.extensions import db


class AccountKind:
    """What an account is. Decides where it appears (Bilanz or GuV) and which forms offer it."""

    MEMBER = 'member'  # a person's Kontokarte; positive Saldo = credit, negative = owes the club
    BANK = 'bank'  # Girokonto, Sparkonto
    CASH = 'cash'  # Barkasse
    ASSET = 'asset'  # other things the club owns, e.g. the drinks stock (Küche)
    FUND = 'fund'  # a separate Kasse or reserve: Burschenkasse, Fuxenkasse, Rücklage Waschmaschine
    PARTNER = 'partner'  # someone the club settles with: AH-Kontingent, Heimverein, Verband
    RESULT = 'result'  # income and spending, split by cost centre (the Excel's "Verschiedene")
    EQUITY = 'equity'  # Eigenkapital
    ALL = (MEMBER, BANK, CASH, ASSET, FUND, PARTNER, RESULT, EQUITY)
    LABELS = {
        MEMBER: 'Kontokarte',
        BANK: 'Bankkonto',
        CASH: 'Barkasse',
        ASSET: 'Bestand',
        FUND: 'Kasse/Rücklage',
        PARTNER: 'Partner',
        RESULT: 'Einnahmen/Ausgaben',
        EQUITY: 'Eigenkapital',
    }
    # Where money is paid in or out (payments, Auslagen paid back).
    MONEY = (BANK, CASH)


class JournalKind:
    CHARGE = 'charge'  # a member is charged (Beitrag, Veranstaltung)
    PAYMENT = 'payment'  # a member pays (bank transfer, cash)
    EXPENSE = 'expense'  # an Auslage: a member paid for the club
    MANUAL = 'manual'  # any other booking between two accounts
    SPLIT = 'split'
    SETTLEMENT = 'settlement'  # laundry, drinks
    OPENING = 'opening'
    REVERSAL = 'reversal'
    LABELS = {
        CHARGE: 'Belastung',
        PAYMENT: 'Zahlung',
        EXPENSE: 'Auslage',
        MANUAL: 'Buchung',
        SPLIT: 'Umlage',
        SETTLEMENT: 'Abrechnung',
        OPENING: 'Eröffnung',
        REVERSAL: 'Storno',
    }


class FinBook(db.Model):
    """A set of books that balance on their own. One for now (the Aktivenkasse)."""

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(40), unique=True, nullable=False)
    label = db.Column(db.String(80), nullable=False)


class FinAccount(db.Model):
    __table_args__ = (
        db.UniqueConstraint('book_id', 'member_id', name='one_card_per_member'),
        db.UniqueConstraint('book_id', 'key', name='book_key'),
        db.CheckConstraint("(kind = 'member') = (member_id IS NOT NULL)", name='member_iff_card'),
    )

    id = db.Column(db.Integer, primary_key=True)
    book_id = db.Column(db.Integer, db.ForeignKey('fin_book.id'), nullable=False)
    kind = db.Column(db.String(10), nullable=False)
    key = db.Column(db.String(40))  # for accounts the code refers to, e.g. 'giro', 'heimverein'
    name = db.Column(db.String(80), nullable=False)
    member_id = db.Column(db.Integer, db.ForeignKey('member.id'), index=True)
    # Entries on this account name a cost centre (like the Excel's "Verschiedene" sub-ledger).
    needs_cost_centre = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    archived_at = db.Column(UTCDateTime)

    member = db.relationship('Member')

    @property
    def kind_label(self):
        return AccountKind.LABELS.get(self.kind, self.kind)


class FinCategory(db.Model):
    """A cost centre (Kostenstelle): Kneipen, Stiftungsfest, Renovierung, ..."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    archived_at = db.Column(UTCDateTime)


class FinJournal(db.Model):
    """One booking (a row of the Excel journal), made of one or more entries. Never edited."""

    __table_args__ = (
        db.CheckConstraint("(kind = 'reversal') = (reverses_journal_id IS NOT NULL)", name='reversal_link'),
    )

    id = db.Column(db.Integer, primary_key=True)
    book_id = db.Column(db.Integer, db.ForeignKey('fin_book.id'), nullable=False, index=True)
    kind = db.Column(db.String(12), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    booked_on = db.Column(db.Date, nullable=False, index=True)
    beleg = db.Column(db.String(20))  # receipt number, e.g. G12 (bank), K3 (cash), B7 (internal)
    semester_id = db.Column(db.Integer, db.ForeignKey('semester.id'))
    source_type = db.Column(db.String(30))
    source_id = db.Column(db.Integer)
    idempotency_key = db.Column(db.String(80), unique=True)
    reverses_journal_id = db.Column(db.Integer, db.ForeignKey('fin_journal.id'), unique=True)
    posted_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))  # NULL = server operator (CLI)
    cosigned_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    posted_at = db.Column(UTCDateTime, nullable=False, default=utcnow)
    note = db.Column(db.Text)

    entries = db.relationship('FinEntry', back_populates='journal', lazy='selectin', order_by='FinEntry.id')
    posted_by = db.relationship('User', foreign_keys=[posted_by_user_id])
    cosigned_by = db.relationship('User', foreign_keys=[cosigned_by_user_id])
    reverses = db.relationship('FinJournal', remote_side=[id], foreign_keys=[reverses_journal_id])
    reversed_by = db.relationship('FinJournal', foreign_keys=[reverses_journal_id], uselist=False, viewonly=True)
    semester = db.relationship('Semester')

    @property
    def kind_label(self):
        return JournalKind.LABELS.get(self.kind, self.kind)

    @property
    def total_cents(self):
        return sum(entry.amount_cents for entry in self.entries)


class FinEntry(db.Model):
    """Money moving from one account to another: debit (Soll) gets it, credit (Haben) gives it."""

    __table_args__ = (
        db.CheckConstraint('amount_cents > 0', name='positive_amount'),
        db.CheckConstraint('debit_account_id != credit_account_id', name='two_accounts'),
    )

    id = db.Column(db.Integer, primary_key=True)
    journal_id = db.Column(db.Integer, db.ForeignKey('fin_journal.id'), nullable=False, index=True)
    debit_account_id = db.Column(db.Integer, db.ForeignKey('fin_account.id'), nullable=False, index=True)
    credit_account_id = db.Column(db.Integer, db.ForeignKey('fin_account.id'), nullable=False, index=True)
    amount_cents = db.Column(db.Integer, nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('fin_category.id'), index=True)
    memo = db.Column(db.String(200))

    journal = db.relationship('FinJournal', back_populates='entries')
    debit_account = db.relationship('FinAccount', foreign_keys=[debit_account_id], lazy='joined')
    credit_account = db.relationship('FinAccount', foreign_keys=[credit_account_id], lazy='joined')
    category = db.relationship('FinCategory', lazy='joined')


# A semester with bookings is kept (core/org: delete_semester).
add_semester_reference(FinJournal.semester_id)
