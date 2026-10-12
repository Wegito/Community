from wtforms import StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired

from community.core.forms import FlaskForm


class AnnouncementForm(FlaskForm):
    title = StringField('Titel', validators=[DataRequired()])
    body = TextAreaField('Text', validators=[DataRequired()])
    submit = SubmitField('Speichern')
