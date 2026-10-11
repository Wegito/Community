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


class ForgotPasswordForm(FlaskForm):
    email = EmailField(
        'Deine Login-E-Mail-Adresse',
        filters=[normalize_email],
        validators=[DataRequired(), Email(), Length(max=255)],
    )
    submit = SubmitField('Link anfordern')


class NewPasswordForm(FlaskForm):
    password = PasswordField(
        'Neues Passwort',
        validators=[
            DataRequired(),
            Length(
                min=MIN_PASSWORD_LENGTH,
                max=MAX_PASSWORD_LENGTH,
                message=f'Mindestens {MIN_PASSWORD_LENGTH} und höchstens {MAX_PASSWORD_LENGTH} Zeichen.',
            ),
        ],
    )
    confirm = PasswordField(
        'Neues Passwort wiederholen',
        validators=[DataRequired(), EqualTo('password', message='Passwörter stimmen nicht überein.')],
    )
    submit = SubmitField('Passwort speichern')


class ChangePasswordForm(NewPasswordForm):
    current_password = PasswordField('Aktuelles Passwort', validators=[DataRequired(), Length(max=MAX_PASSWORD_LENGTH)])
    submit = SubmitField('Passwort ändern')


class ChangeEmailForm(FlaskForm):
    login_email = EmailField(
        'Neue Login-E-Mail-Adresse',
        filters=[normalize_email],
        validators=[DataRequired(), Email(), Length(max=255)],
    )
    password = PasswordField('Aktuelles Passwort', validators=[DataRequired(), Length(max=MAX_PASSWORD_LENGTH)])
    submit = SubmitField('Bestätigungslink schicken')


class ConfirmForm(FlaskForm):
    """CSRF-only form for the button that confirms a new login email."""

    submit = SubmitField('Bestätigen')
