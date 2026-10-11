"""Notifications: the in-app bell and email, per kind and channel (roadmap §7.4).

Code declares the kinds with declare_kind() and calls notify(member, kind, title). Members
choose per kind and channel in /settings/notifications; mandatory kinds (password reset)
ignore those choices. Emails go through the job queue, never from the request itself.
"""

from dataclasses import dataclass

from flask import g, has_request_context
from flask_login import current_user
from sqlalchemy import func, select, update

from community.core.db import UTCDateTime
from community.core.platform import audit
from community.core.time import utcnow
from community.extensions import db

from .mail import absolute_link, absolute_url, queue_mail

IN_APP = 'in_app'
EMAIL = 'email'
CHANNELS = (IN_APP, EMAIL)
CHANNEL_LABELS = {IN_APP: 'In der App', EMAIL: 'E-Mail'}


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('member.id'), nullable=False, index=True)
    kind = db.Column(db.String(40), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    link = db.Column(db.String(500))  # app path like "/org/offices/senior"
    created_at = db.Column(UTCDateTime, nullable=False, default=utcnow)
    read_at = db.Column(UTCDateTime)


class NotificationPref(db.Model):
    """A member's choice for one kind and channel. No row = the kind's default."""

    __tablename__ = 'notification_pref'

    member_id = db.Column(db.Integer, db.ForeignKey('member.id'), primary_key=True)
    kind = db.Column(db.String(40), primary_key=True)
    channel = db.Column(db.String(10), primary_key=True)
    enabled = db.Column(db.Boolean, nullable=False)


@dataclass(frozen=True)
class NotificationKind:
    key: str
    label: str
    mandatory: bool = False
    channels: tuple = CHANNELS
    default_email: bool = True

    def default(self, channel):
        return self.default_email if channel == EMAIL else True


KINDS = {}


def declare_kind(kind, label, *, mandatory=False, channels=CHANNELS, default_email=True):
    unknown = set(channels) - set(CHANNELS)
    if unknown or not channels:
        raise ValueError(f'Notification kind {kind!r}: invalid channels {channels!r}')
    definition = NotificationKind(kind, label, mandatory, tuple(channels), default_email)
    existing = KINDS.get(kind)
    if existing is not None and existing != definition:
        raise ValueError(f'Notification kind {kind!r} declared twice with different settings')
    KINDS[kind] = definition
    return kind


declare_kind('welcome', 'Willkommen nach der Registrierung')
declare_kind('new_member', 'Neues Mitglied (für den Fuxmajor)')
declare_kind('member_exit', 'Austritt eines Mitglieds (für den Kassenwart)')
declare_kind('password_reset', 'Passwort zurücksetzen', mandatory=True, channels=(EMAIL,))
declare_kind('email_changed', 'E-Mail-Adresse geändert', mandatory=True, channels=(EMAIL,))
declare_kind('invite', 'Einladung', channels=(EMAIL,))
declare_kind('office_assigned', 'Amt übernommen')
declare_kind('rank_changed', 'Rang geändert')


def kind_or_error(kind):
    try:
        return KINDS[kind]
    except KeyError:
        raise ValueError(f'Unknown notification kind {kind!r}; declare it with declare_kind()') from None


def _stored_prefs(member):
    rows = db.session.scalars(select(NotificationPref).where(NotificationPref.member_id == member.id))
    return {(row.kind, row.channel): row.enabled for row in rows}


def channel_enabled(member, kind, channel, stored=None):
    """Whether `member` gets `kind` on `channel`: mandatory > own choice > the kind's default."""
    definition = kind_or_error(kind)
    if channel not in definition.channels:
        return False
    if definition.mandatory:
        return True
    if stored is None:
        pref = db.session.get(NotificationPref, (member.id, kind, channel))
        return definition.default(channel) if pref is None else pref.enabled
    return stored.get((kind, channel), definition.default(channel))


def mail_address(member):
    """The login email, or the contact email for members without a login."""
    user = member.user
    return (user.login_email if user is not None else None) or member.email


def notify(member, kind, title, *, link=None, mail_template=None, mail_context=None):
    """Tell `member` about something, on the channels they chose. Commits nothing.

    link: app path shown in the bell and (as a full URL) in the mail.
    mail_template: templates/mail/<name>.txt; default 'notification' (title and link).
    Returns the in-app Notification, or None if that channel is off.
    """
    definition = kind_or_error(kind)
    notification = None
    if channel_enabled(member, kind, IN_APP):
        notification = Notification(member_id=member.id, kind=kind, title=title[:200], link=link)
        db.session.add(notification)
    address = mail_address(member)
    if address and channel_enabled(member, kind, EMAIL):
        context = {
            'name': member.first_name,
            'title': title,
            'link_url': absolute_link(link),
            'settings_url': None if definition.mandatory else _settings_url(),
            **(mail_context or {}),
        }
        queue_mail(address, mail_template or 'notification', **context)
    return notification


def _settings_url():
    try:
        return absolute_url('notifications.settings')
    except RuntimeError:  # no BASE_URL and no request (CLI): the mail goes out without the link
        return None


def preferences(member):
    """Rows for the settings page: (kind definition, {channel: enabled or None if not offered})."""
    stored = _stored_prefs(member)
    rows = []
    for definition in KINDS.values():
        state = {
            channel: channel_enabled(member, definition.key, channel, stored)
            if channel in definition.channels
            else None
            for channel in CHANNELS
        }
        rows.append((definition, state))
    return rows


def save_preferences(member, enabled, *, actor=None):
    """Store the member's choices. `enabled` is the set of (kind, channel) pairs switched on;
    every other optional pair is switched off. Mandatory kinds are not stored. Commits nothing."""
    stored = _stored_prefs(member)
    changed = {}
    for definition in KINDS.values():
        if definition.mandatory:
            continue
        for channel in definition.channels:
            want = (definition.key, channel) in enabled
            if channel_enabled(member, definition.key, channel, stored) != want:
                changed[f'{definition.key}.{channel}'] = want
            pref = db.session.get(NotificationPref, (member.id, definition.key, channel))
            if pref is None:
                db.session.add(
                    NotificationPref(member_id=member.id, kind=definition.key, channel=channel, enabled=want)
                )
            else:
                pref.enabled = want
    if changed:
        audit.record('notify.preferences', member, actor=actor, diff=changed)
    return changed


def notifications_for(member, limit=100):
    return db.session.scalars(
        select(Notification)
        .where(Notification.member_id == member.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    ).all()


def unread_count(member):
    return db.session.scalar(
        select(func.count()).where(Notification.member_id == member.id, Notification.read_at.is_(None))
    )


def mark_read(notification):
    if notification.read_at is None:
        notification.read_at = utcnow()
    return notification


def mark_all_read(member):
    result = db.session.execute(
        update(Notification)
        .where(Notification.member_id == member.id, Notification.read_at.is_(None))
        .values(read_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


def current_unread_count():
    """For the bell in layout.html: unread notifications of the logged-in member (0 otherwise)."""
    if not has_request_context() or not current_user.is_authenticated:
        return 0
    if '_unread_notifications' not in g:
        g._unread_notifications = unread_count(current_user.member)
    return g._unread_notifications
