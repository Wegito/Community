from wtforms import (
    BooleanField,
    DateField,
    DecimalField,
    HiddenField,
    RadioField,
    SelectField,
    StringField,
    SubmitField,
    ValidationError,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from community.core.forms import FlaskForm

from .models import DRY_PROGRAMS, MachineStatus, PriceKind, PriceTier


class UseForm(FlaskForm):
    """The questions of the old Google Form, without name and email (known from the login)."""

    washes = RadioField(
        'Waschen',
        choices=[(1, 'Eine Maschine'), (2, 'Beide Maschinen'), (0, 'Nicht waschen')],
        coerce=int,
        default=1,
    )
    dried = BooleanField('Trocknen')
    dry_program = SelectField('Trocknerprogramm', choices=[('', '— bitte wählen —')] + [(p, p) for p in DRY_PROGRAMS])
    container = StringField('Wanne/Box (optional)', validators=[Optional(), Length(max=80)])
    submission_id = HiddenField()
    submit = SubmitField('Eintragen')

    def validate_dried(self, field):
        if not field.data and not self.washes.data:
            raise ValidationError('Bitte Waschen oder Trocknen auswählen.')

    def validate_dry_program(self, field):
        if self.dried.data and not field.data:
            raise ValidationError('Bitte ein Trocknerprogramm wählen.')


class VoidForm(FlaskForm):
    reason = StringField('Grund', validators=[Optional(), Length(max=200)])
    submit = SubmitField('Stornieren')


class ButtonForm(FlaskForm):
    """CSRF-only form for single-button actions."""


class PriceForm(FlaskForm):
    tier = SelectField('Preisgruppe', choices=list(PriceTier.LABELS.items()))
    kind = SelectField('Für', choices=list(PriceKind.LABELS.items()))
    price = DecimalField('Preis (€)', places=2, validators=[DataRequired(), NumberRange(min=0, max=100)])
    valid_from = DateField('Gilt ab', validators=[DataRequired()])
    submit = SubmitField('Preis speichern')


class MachineStatusForm(FlaskForm):
    status = SelectField('Status', choices=list(MachineStatus.LABELS.items()))
    submit = SubmitField('Ändern')
