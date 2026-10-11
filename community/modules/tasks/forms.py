from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional


def optional_int(value):
    """SelectField coercion: '' or None means "nobody"."""
    if value in (None, '', 'None'):
        return None
    return int(value)


class ChoreForm(FlaskForm):
    title = StringField('Aufgabe', validators=[DataRequired(), Length(max=200)])
    assigned_to_id = SelectField('Zuständig', coerce=optional_int, validate_choice=True)
    due_date = DateField('Fällig am', validators=[Optional()])
    is_done = BooleanField('Erledigt')
    submit = SubmitField('Speichern')


class ToggleDoneForm(FlaskForm):
    """Button form for the assignee to mark a chore done or open again (CSRF only)."""
