from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length

from community.core.identity.models import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, normalize_email


def strip(value):
    return value.strip() if value else value


class RegisterForm(FlaskForm):
    name = StringField('Name', filters=[strip], validators=[DataRequired(), Length(max=120)])
    email = EmailField('E-Mail', filters=[normalize_email], validators=[DataRequired(), Email(), Length(max=255)])
    invite = StringField('Invite-Code', filters=[strip], validators=[DataRequired()])
    password = PasswordField(
        'Passwort',
        validators=[
            DataRequired(),
            Length(
                min=MIN_PASSWORD_LENGTH,
                max=MAX_PASSWORD_LENGTH,
                message=f'Mindestens {MIN_PASSWORD_LENGTH} Zeichen.',
            ),
        ],
    )
    confirm = PasswordField(
        'Bestätigen', validators=[DataRequired(), EqualTo('password', message='Passwörter stimmen nicht überein.')]
    )
    submit = SubmitField('Registrieren')


class LoginForm(FlaskForm):
    email = EmailField('E-Mail', filters=[normalize_email], validators=[DataRequired(), Email()])
    password = PasswordField('Passwort', validators=[DataRequired(), Length(max=MAX_PASSWORD_LENGTH)])
    remember = BooleanField('Angemeldet bleiben')
    submit = SubmitField('Login')
