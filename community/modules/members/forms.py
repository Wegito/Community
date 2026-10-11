from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, EmailField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional, Regexp

from community.core.identity.models import normalize_email


def strip(value):
    return value.strip() if value else value


class ProfileForm(FlaskForm):
    """What every member may change on their own profile."""

    biername = StringField('Biername', filters=[strip], validators=[Optional(), Length(max=80)])
    phone = StringField(
        'Telefon',
        filters=[strip],
        validators=[
            Optional(),
            Length(max=40),
            Regexp(r'^[0-9 +()/.-]+$', message='Nur Ziffern, Leerzeichen und + ( ) / . -'),
        ],
    )
    address = TextAreaField('Adresse', filters=[strip], validators=[Optional(), Length(max=300)])
    show_phone = BooleanField('Telefonnummer für Mitglieder mit Kontaktrecht sichtbar')
    show_address = BooleanField('Adresse für Mitglieder mit Kontaktrecht sichtbar')
    submit = SubmitField('Speichern')


class MemberForm(ProfileForm):
    """Everything members.edit_any holders may change."""

    first_name = StringField('Vorname', filters=[strip], validators=[DataRequired(), Length(max=80)])
    last_name = StringField('Nachname', filters=[strip], validators=[Optional(), Length(max=80)])
    email = EmailField('Kontakt-E-Mail', filters=[normalize_email], validators=[Optional(), Email(), Length(max=255)])
    joined_on = DateField('Eingetreten am', validators=[Optional()])
