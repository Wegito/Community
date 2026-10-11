from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired


class AnnouncementForm(FlaskForm):
    title = StringField('Titel', validators=[DataRequired()])
    body = TextAreaField('Text', validators=[DataRequired()])
    submit = SubmitField('Speichern')
