from flask_wtf import FlaskForm
from wtforms import DateTimeLocalField, SelectField, StringField, SubmitField, ValidationError
from wtforms.validators import DataRequired, Length


class BookingForm(FlaskForm):
    resource_id = SelectField('Ressource', coerce=int, validators=[DataRequired()])
    start = DateTimeLocalField('Start', format='%Y-%m-%dT%H:%M', validators=[DataRequired()])
    end = DateTimeLocalField('Ende', format='%Y-%m-%dT%H:%M', validators=[DataRequired()])
    submit = SubmitField('Buchen')

    def validate_end(self, field):
        if self.start.data and field.data and field.data <= self.start.data:
            raise ValidationError('Das Ende muss nach dem Start liegen.')


class ResourceForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=120)])
    submit = SubmitField('Anlegen')


class ButtonForm(FlaskForm):
    """CSRF-only form for single-button actions (cancel, activate)."""
