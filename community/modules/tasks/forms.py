from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, StringField, SubmitField
from wtforms.validators import DataRequired


class ChoreForm(FlaskForm):
    title = StringField('Aufgabe', validators=[DataRequired()])
    due_date = DateField('Fällig am')
    is_done = BooleanField('Erledigt')
    submit = SubmitField('Speichern')
