from wtforms import (
    BooleanField,
    DateField,
    HiddenField,
    RadioField,
    SelectField,
    SelectMultipleField,
    StringField,
    SubmitField,
    TextAreaField,
    ValidationError,
)
from wtforms.validators import DataRequired, Length, Optional
from wtforms.widgets import CheckboxInput, ListWidget

from community.core.forms import FlaskForm
from community.core.money import parse_cents

from .models import AccountKind
from .services import NAME_MAX, TITLE_MAX


def parse_amount(text):
    """German or English notation; must be more than 0. Raises ValidationError."""
    try:
        cents = parse_cents(text)
    except ValueError:
        raise ValidationError('Bitte einen Betrag wie 12,50 eingeben.') from None
    if cents <= 0:
        raise ValidationError('Der Betrag muss größer als 0 sein.')
    return cents


class BookingForm(FlaskForm):
    """One row of the Kassenbuch: date, Beleg, text, amount, Soll and Haben (and a cost centre)."""

    booked_on = DateField('Datum', validators=[DataRequired()])
    beleg = StringField('Beleg (optional)', validators=[Optional(), Length(max=20)])
    title = StringField('Buchungstext', validators=[DataRequired(), Length(max=TITLE_MAX)])
    amount = StringField('Betrag (€)', validators=[DataRequired()])
    debit = SelectField('Soll (bekommt das Geld)', validators=[DataRequired()], validate_choice=True)
    credit = SelectField('Haben (gibt das Geld)', validators=[DataRequired()], validate_choice=True)
    category = SelectField('Kostenstelle', validators=[Optional()], validate_choice=True)
    note = TextAreaField('Notiz (optional)', validators=[Optional(), Length(max=1000)])
    submit = SubmitField('Buchen')

    def validate_amount(self, field):
        field.cents = parse_amount(field.data)

    def validate_credit(self, field):
        if field.data and field.data == self.debit.data:
            raise ValidationError('Soll und Haben müssen verschieden sein.')


class ReverseForm(FlaskForm):
    reason = StringField('Grund', validators=[DataRequired(), Length(max=500)])
    submit = SubmitField('Stornieren')


KIND_CHOICES = [(kind, AccountKind.LABELS[kind]) for kind in AccountKind.ALL if kind != AccountKind.MEMBER]


class AccountForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=NAME_MAX)])
    kind = SelectField('Art', choices=KIND_CHOICES)
    needs_cost_centre = BooleanField('Buchungen brauchen eine Kostenstelle')
    submit = SubmitField('Konto anlegen')


class AccountEditForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=NAME_MAX)])
    needs_cost_centre = BooleanField('Buchungen brauchen eine Kostenstelle')
    archived = BooleanField('Archiviert (nicht mehr zum Buchen anbieten)')
    submit = SubmitField('Speichern')


class CategoryForm(FlaskForm):
    name = StringField('Neue Kostenstelle', validators=[DataRequired(), Length(max=NAME_MAX)])
    submit = SubmitField('Anlegen')


class CategoryEditForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=NAME_MAX)])
    archived = BooleanField('Archiviert')
    submit = SubmitField('Speichern')


class MultiCheckboxField(SelectMultipleField):
    """A list of checkboxes; rendered by finance/_checkboxes.html."""

    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


class UmlageForm(FlaskForm):
    """Charge many members at once; a Kasse can pay part of the share for some ranks."""

    title = StringField('Buchungstext', validators=[DataRequired(), Length(max=TITLE_MAX)])
    booked_on = DateField('Datum', validators=[DataRequired()])
    beleg = StringField('Beleg (optional)', validators=[Optional(), Length(max=20)])
    target = SelectField('Gegenkonto (bekommt das Geld)', coerce=int)
    category = SelectField('Kostenstelle', validators=[Optional()])
    mode = RadioField(
        'Betrag', choices=[('person', 'pro Person'), ('total', 'Gesamtbetrag, gleich aufgeteilt')], default='person'
    )
    amount = StringField('Betrag (€)', validators=[DataRequired()])
    rank_ids = MultiCheckboxField('Alle mit Rang', coerce=int)
    group_id = SelectField('Alle aus Gruppe', coerce=int)
    member_ids = MultiCheckboxField('Einzeln auswählen', coerce=int)
    subsidy_account = SelectField('Zuschuss aus Kasse (optional)', coerce=int)
    subsidy_rank_ids = MultiCheckboxField('für Rang', coerce=int)
    subsidy_amount = StringField('Zuschuss pro Person (€)', validators=[Optional()])
    token = HiddenField()
    preview = SubmitField('Vorschau')
    confirm = SubmitField('Jetzt buchen')

    def validate_amount(self, field):
        field.cents = parse_amount(field.data)

    def validate_subsidy_amount(self, field):
        field.cents = parse_amount(field.data) if field.data else None
        if self.subsidy_account.data and (not field.cents or not self.subsidy_rank_ids.data):
            raise ValidationError('Für einen Zuschuss bitte Rang und Betrag angeben.')


class RecurringForm(FlaskForm):
    title = StringField('Titel', validators=[DataRequired(), Length(max=120)])
    amount = StringField('Betrag pro Person und Monat (€)', validators=[DataRequired()])
    target = SelectField('Gegenkonto', coerce=int)
    category = SelectField('Kostenstelle', validators=[Optional()])
    rank_keys = MultiCheckboxField('Für alle mit Rang')
    group_id = SelectField('und/oder Gruppe', coerce=int)
    submit = SubmitField('Speichern')

    def validate_amount(self, field):
        field.cents = parse_amount(field.data)


class ButtonForm(FlaskForm):
    """CSRF-only form for single-button actions."""
