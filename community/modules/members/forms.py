from flask import current_app
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    EmailField,
    RadioField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Email, Length, Optional, Regexp

from community.core.identity.models import ExitReason, InvitationChannel, normalize_email

from .services import DELIVERY_NONE, EMAIL_NEEDED


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


class ExitForm(FlaskForm):
    """Austritt: when and why a member left. The note is kept in the audit log only."""

    ended_on = DateField('Ausgeschieden am', validators=[DataRequired()])
    reason = SelectField('Grund', choices=list(ExitReason.LABELS.items()))
    note = TextAreaField('Notiz (nur im Protokoll)', filters=[strip], validators=[Optional(), Length(max=500)])
    submit = SubmitField('Austritt eintragen')


def delivery_choices(*, with_none):
    """How the invitation reaches the new member; QR codes expire sooner (INVITE_QR_HOURS)."""
    config = current_app.config
    choices = [
        (InvitationChannel.LINK, f'Link anzeigen und selbst weitergeben ({config["INVITE_LINK_DAYS"]} Tage gültig)'),
        (InvitationChannel.EMAIL, f'Link per E-Mail schicken ({config["INVITE_LINK_DAYS"]} Tage gültig)'),
        (InvitationChannel.QR, f'QR-Code zum Scannen anzeigen ({config["INVITE_QR_HOURS"]} Stunden gültig)'),
    ]
    if with_none:
        choices.append((DELIVERY_NONE, 'Noch nicht einladen (später auf der Mitgliederseite)'))
    return choices


class InviteForm(FlaskForm):
    """Invite a member without login."""

    delivery = RadioField('Einladung', default=InvitationChannel.LINK)
    email = EmailField(
        'E-Mail-Adresse für die Einladung',
        filters=[normalize_email],
        validators=[Optional(), Email(), Length(max=255)],
    )
    submit = SubmitField('Einladen')

    def __init__(self, *args, with_none=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.delivery.choices = delivery_choices(with_none=with_none)

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False
        if self.delivery.data == InvitationChannel.EMAIL and not self.email.data:
            self.email.errors.append(EMAIL_NEEDED)
            return False
        return True


class NewMemberForm(InviteForm):
    """Neues Mitglied: the member, their starting rank and how to invite them."""

    first_name = StringField('Vorname', filters=[strip], validators=[DataRequired(), Length(max=80)])
    last_name = StringField('Nachname', filters=[strip], validators=[Optional(), Length(max=80)])
    biername = StringField('Biername', filters=[strip], validators=[Optional(), Length(max=80)])
    email = EmailField('Kontakt-E-Mail', filters=[normalize_email], validators=[Optional(), Email(), Length(max=255)])
    joined_on = DateField('Eingetreten am', validators=[DataRequired()])
    rank_id = SelectField('Rang ab Eintritt', coerce=int)
    submit = SubmitField('Mitglied anlegen')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, with_none=True, **kwargs)


class ButtonForm(FlaskForm):
    """CSRF-only form for single-button actions (revoke an invitation)."""
