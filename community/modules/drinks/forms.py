from wtforms import (
    BooleanField,
    DateField,
    HiddenField,
    IntegerField,
    SelectMultipleField,
    StringField,
    SubmitField,
    ValidationError,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional
from wtforms.widgets import CheckboxInput, ListWidget

from community.core.forms import FlaskForm
from community.core.money import parse_cents

from .services import NAME_MAX


def parse_price(text):
    try:
        cents = parse_cents(text)
    except ValueError:
        raise ValidationError('Bitte einen Preis wie 1,20 eingeben.') from None
    if cents < 0:
        raise ValidationError('Der Preis darf nicht negativ sein.')
    return cents


class ProductForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=NAME_MAX)])
    price = StringField('Preis (€)', validators=[DataRequired()])
    counts_as_beer = BooleanField('Zählt als Bier (Bierkönig, Fass-Barometer)')
    volume_ml = IntegerField('Menge in ml (optional, z. B. 500)', validators=[Optional(), NumberRange(1, 5000)])
    submit = SubmitField('Anlegen')

    def validate_price(self, field):
        field.cents = parse_price(field.data)


class ProductEditForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=NAME_MAX)])
    counts_as_beer = BooleanField('Zählt als Bier (Bierkönig, Fass-Barometer)')
    volume_ml = IntegerField('Menge in ml (optional)', validators=[Optional(), NumberRange(1, 5000)])
    archived = BooleanField('Archiviert (wird nicht mehr verkauft)')
    submit = SubmitField('Speichern')


class PriceForm(FlaskForm):
    price = StringField('Neuer Preis (€)', validators=[DataRequired()])
    valid_from = DateField('Gilt ab', validators=[DataRequired()])
    submit = SubmitField('Preis ändern')

    def validate_price(self, field):
        field.cents = parse_price(field.data)


class SheetForm(FlaskForm):
    """The quantities come as fields q-<member id>-<product id>; the routes read them."""

    day = DateField('Abend', validators=[DataRequired()])
    token = HiddenField()
    submit = SubmitField('Striche speichern')


class CorrectForm(FlaskForm):
    reason = StringField('Grund', validators=[DataRequired(), Length(max=200)])
    submit = SubmitField('Korrigieren')


class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


class DeviceForm(FlaskForm):
    name = StringField('Name des Geräts', validators=[DataRequired(), Length(max=80)])
    scopes = MultiCheckboxField('Darf')
    submit = SubmitField('Gerät anlegen')


class ButtonForm(FlaskForm):
    """CSRF-only form for single-button actions."""
