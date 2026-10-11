from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length

from community.core.identity.models import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, normalize_email


class JoinForm(FlaskForm):
    """Create the login from an invitation (/join/<token>)."""

    login_email = EmailField(
        'E-Mail-Adresse für den Login',
        filters=[normalize_email],
        validators=[DataRequired(), Email(), Length(max=255)],
    )
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
        'Passwort wiederholen',
        validators=[DataRequired(), EqualTo('password', message='Passwörter stimmen nicht überein.')],
    )
    submit = SubmitField('Konto anlegen')


class LoginForm(FlaskForm):
    email = EmailField('E-Mail', filters=[normalize_email], validators=[DataRequired(), Email()])
    password = PasswordField('Passwort', validators=[DataRequired(), Length(max=MAX_PASSWORD_LENGTH)])
    remember = BooleanField('Angemeldet bleiben')
    submit = SubmitField('Login')
