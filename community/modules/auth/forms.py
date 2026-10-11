from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length


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
