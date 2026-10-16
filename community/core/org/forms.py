from wtforms import BooleanField, DateField, SelectField, StringField, SubmitField, TextAreaField, ValidationError
from wtforms.validators import DataRequired, Length, Optional, Regexp

from community.core.forms import FlaskForm

from .models import GroupKind, SemesterKind, SemesterStatus


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


class SemesterEditForm(SemesterForm):
    submit = SubmitField('Speichern')


class SemesterStatusForm(FlaskForm):
    status = SelectField('Status', choices=list(SemesterStatus.LABELS.items()))
    submit = SubmitField('Ändern')


class RankChangeForm(FlaskForm):
    rank_id = SelectField('Neuer Rang', coerce=int)
    valid_from = DateField('Ab', validators=[DataRequired()])
    convent_ref = StringField('Beschluss (Convent)', validators=[Optional(), Length(max=80)])
    note = StringField('Notiz', validators=[Optional(), Length(max=200)])
    submit = SubmitField('Rang ändern')


class FirstRankForm(FlaskForm):
    """The date for "Ohne Rang"; the rank of each member is a select named rank-<member id>."""

    valid_from = DateField('Ab', validators=[DataRequired()])
    submit = SubmitField('Ränge setzen')


class RankLabelForm(FlaskForm):
    label = StringField('Bezeichnung', validators=[DataRequired(), Length(max=80)])
    abbreviation = StringField('Kürzel', validators=[DataRequired(), Length(max=10)])
    submit = SubmitField('Speichern')


def optional_int(value):
    if value in (None, '', 'None'):
        return None
    return int(value)


class OfficeAssignForm(FlaskForm):
    member_id = SelectField('Mitglied', coerce=int)
    valid_from = DateField('Ab', validators=[DataRequired()])
    semester_id = SelectField('Semester', coerce=optional_int, validate_choice=False)
    elected_on = DateField('Gewählt am', validators=[Optional()])
    convent_ref = StringField('Beschluss (Convent)', validators=[Optional(), Length(max=80)])
    is_acting = BooleanField('Kommissarisch')
    end_previous = BooleanField('Bisherige Inhaber zum Vortag beenden', default=True)
    submit = SubmitField('Amt besetzen')


class OfficeEndForm(FlaskForm):
    valid_to = DateField('Letzter Tag', validators=[DataRequired()])
    reason = StringField('Grund', validators=[Optional(), Length(max=200)])
    submit = SubmitField('Beenden')


class OfficeDescriptionForm(FlaskForm):
    description_md = TextAreaField('Aufgaben (Übergabe-Notiz)', validators=[Length(max=10000)])
    submit = SubmitField('Speichern')


class GroupForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=80)])
    kind = SelectField('Art', choices=[(k, v) for k, v in GroupKind.LABELS.items() if k != GroupKind.RESIDENCE])
    submit = SubmitField('Gruppe anlegen')


class GroupAddForm(FlaskForm):
    member_id = SelectField('Mitglied', coerce=int)
    valid_from = DateField('Ab', validators=[DataRequired()])
    is_lead = BooleanField('Leitung')
    submit = SubmitField('Hinzufügen')


class ButtonForm(FlaskForm):
    """CSRF-only form for single-button actions."""
