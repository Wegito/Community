from wtforms import DateTimeLocalField, SelectField, StringField, SubmitField, ValidationError
from wtforms.validators import DataRequired, Length

from community.core.forms import FlaskForm
from community.core.time import exists_locally, local_to_utc

SKIPPED_TIME = 'Diese Uhrzeit gibt es wegen der Zeitumstellung nicht.'


class BookingForm(FlaskForm):
    resource_id = SelectField('Ressource', coerce=int, validators=[DataRequired()])
    start = DateTimeLocalField('Start', format='%Y-%m-%dT%H:%M', validators=[DataRequired()])
    end = DateTimeLocalField('Ende', format='%Y-%m-%dT%H:%M', validators=[DataRequired()])
    submit = SubmitField('Buchen')

    def validate_start(self, field):
        if field.data and not exists_locally(field.data):
            raise ValidationError(SKIPPED_TIME)

    def validate_end(self, field):
        if field.data and not exists_locally(field.data):
            raise ValidationError(SKIPPED_TIME)
        # Compare the stored (UTC) values: around the clock change, local order can differ.
        if self.start.data and field.data and local_to_utc(field.data) <= local_to_utc(self.start.data):
            raise ValidationError('Das Ende muss nach dem Start liegen.')


class ResourceForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=120)])
    submit = SubmitField('Anlegen')


class ButtonForm(FlaskForm):
    """CSRF-only form for single-button actions (cancel, activate)."""
