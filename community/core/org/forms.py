from flask_wtf import FlaskForm
from wtforms import DateField, SelectField, StringField, SubmitField, ValidationError
from wtforms.validators import DataRequired, Length, Optional, Regexp

from .models import SemesterKind, SemesterStatus


class SemesterForm(FlaskForm):
    code = StringField(
        'Kürzel',
        validators=[
            DataRequired(),
            Length(max=10),
            Regexp(r'^(WS\d\d/\d\d|SS\d\d)$', message='z. B. WS26/27 oder SS27'),
        ],
    )
    kind = SelectField(
        'Art', choices=[(SemesterKind.WINTER, 'Wintersemester'), (SemesterKind.SUMMER, 'Sommersemester')]
    )
    starts_on = DateField('Beginn', validators=[DataRequired()])
    ends_on = DateField('Ende', validators=[DataRequired()])
    submit = SubmitField('Semester anlegen')

    def validate_ends_on(self, field):
        if self.starts_on.data and field.data and field.data <= self.starts_on.data:
            raise ValidationError('Das Ende muss nach dem Beginn liegen.')


class SemesterStatusForm(FlaskForm):
    status = SelectField('Status', choices=list(SemesterStatus.LABELS.items()))
    submit = SubmitField('Ändern')


class RankChangeForm(FlaskForm):
    rank_id = SelectField('Neuer Rang', coerce=int)
    valid_from = DateField('Ab', validators=[DataRequired()])
    convent_ref = StringField('Beschluss (Convent)', validators=[Optional(), Length(max=80)])
    note = StringField('Notiz', validators=[Optional(), Length(max=200)])
    submit = SubmitField('Rang ändern')


class RankLabelForm(FlaskForm):
    label = StringField('Bezeichnung', validators=[DataRequired(), Length(max=80)])
    abbreviation = StringField('Kürzel', validators=[DataRequired(), Length(max=10)])
    submit = SubmitField('Speichern')
