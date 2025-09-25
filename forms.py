from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, TextAreaField, BooleanField, DateField, DateTimeLocalField
from wtforms.validators import DataRequired, Email, Length, EqualTo


class RegisterForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=120)])
    email = StringField('E-Mail', validators=[DataRequired(), Email()])
    invite = StringField('Invite‑Code', validators=[DataRequired()])
    password = PasswordField('Passwort', validators=[DataRequired(), Length(min=6)])
    confirm = PasswordField('Bestätigen', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Registrieren')


class LoginForm(FlaskForm):
    email = StringField('E-Mail', validators=[DataRequired(), Email()])
    password = PasswordField('Passwort', validators=[DataRequired()])
    submit = SubmitField('Login')


class AnnouncementForm(FlaskForm):
    title = StringField('Titel', validators=[DataRequired()])
    body = TextAreaField('Text', validators=[DataRequired()])
    submit = SubmitField('Speichern')


class ChoreForm(FlaskForm):
    title = StringField('Aufgabe', validators=[DataRequired()])
    due_date = DateField('Fällig am')
    is_done = BooleanField('Erledigt')
    submit = SubmitField('Speichern')


class BookingForm(FlaskForm):
    resource = StringField('Ressource', validators=[DataRequired()])
    start = DateTimeLocalField('Start', format='%Y-%m-%dT%H:%M', validators=[DataRequired()])
    end = DateTimeLocalField('Ende', format='%Y-%m-%dT%H:%M', validators=[DataRequired()])
    submit = SubmitField('Buchen')