from flask_wtf import FlaskForm
from wtforms import DateTimeLocalField, StringField, SubmitField
from wtforms.validators import DataRequired


class BookingForm(FlaskForm):
    resource = StringField('Ressource', validators=[DataRequired()])
    start = DateTimeLocalField('Start', format='%Y-%m-%dT%H:%M', validators=[DataRequired()])
    end = DateTimeLocalField('Ende', format='%Y-%m-%dT%H:%M', validators=[DataRequired()])
    submit = SubmitField('Buchen')
