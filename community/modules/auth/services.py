"""Joining with a personal invitation (roadmap §3.3); the own password and login email (M1-8).

Reset and email-change links are signed with SECRET_KEY (itsdangerous) instead of being stored.
Each token carries what must not change before it is used, so it dies on its own:
  password reset  {'uid', 'v': session_version, 'h': fingerprint of the password hash,
                   'e': fingerprint of the login email}
                  Dies when used (the reset raises session_version), after any other password
                  change, "log out everywhere" or a locked login, when the login email changes
                  (the link went to the old mailbox), and after PASSWORD_RESET_MAX_AGE_MINUTES.
  email change    {'uid', 'email': new address, 'v', 'from': fingerprint of the login email}
                  Dies when used (the login email is no longer 'from'), after a password or
                  session change, and after EMAIL_CHANGE_MAX_AGE.
"""

import hashlib
from datetime import timedelta

from flask import current_app, url_for
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from community.core.club import club_name
from community.core.identity.models import User, normalize_email
from community.core.identity.services import (
    accept_invitation,
    end_all_sessions,
    find_user_by_login_email,
    login_email_taken,
)
from community.core.org import services as org
from community.core.platform import audit
from community.core.platform.jobs import Job
from community.core.platform.mail import absolute_url, queue_mail
from community.core.platform.notify import notify
from community.core.time import utcnow
from community.extensions import db

# The Amt that looks after new members and is told when one joins.
NEW_MEMBER_OFFICE = 'fuxmajor'

RESET_SALT = 'password-reset'
EMAIL_CHANGE_SALT = 'email-change'
EMAIL_CHANGE_MAX_AGE = timedelta(hours=24)
# At most one mail per account (and per new address) in each window, however often a form is
# sent: nobody can flood a mailbox through "Passwort vergessen?".
MAIL_WINDOW = timedelta(minutes=10)
# Our tokens have a few hundred characters at most; longer input is not worth checking.
MAX_TOKEN_LENGTH = 1000

INVALID_LINK = 'Link ungültig oder abgelaufen – bitte einen neuen anfordern.'
WRONG_PASSWORD = 'Das aktuelle Passwort stimmt nicht.'  # noqa: S105 (a message, not a password)
TOO_MANY_ATTEMPTS = 'Zu viele Fehlversuche. Bitte in einigen Minuten noch einmal versuchen.'
LOGIN_EMAIL_IN_USE = 'Diese E-Mail-Adresse gehört schon zu einem anderen Konto oder Mitglied.'
SAME_EMAIL = 'Das ist schon deine Login-E-Mail-Adresse.'


class AccountError(Exception):
    """A rule was broken; the message is shown to the user (German)."""


class WrongPassword(AccountError):
    """The current password was wrong, or there were too many wrong ones."""


class LoginEmailInUse(AccountError):
    """The address is another account's login email or another member's contact email."""


class InvalidLink(AccountError):
    """The link is forged, expired or used, or the account changed since it was sent."""


# --- Joining ---------------------------------------------------------------------


def join(invitation, login_email, password):
    """An invited member creates their login (/join/<token>). Returns the User. Commits nothing.

    Accepts the invitation, welcomes the member and tells the Fuxmajor (bell and mail, as each
    of them chose). The caller logs the new user in after the commit.
    Raises InvitationError or EmailInUse (community.core.identity.services).
    """
    user = accept_invitation(invitation, login_email, password)
    user.last_login_at = utcnow()  # logged in right away
    member = user.member
    club = club_name()
    notify(
        member,
        'welcome',
        f'Willkommen bei {club}, {member.first_name}! Ergänze in deinem Profil Biername und Telefon.',
        link=url_for('members.profile'),
    )
    for holder in new_member_officers(exclude=member):
        notify(
            holder,
            'new_member',
            f'{member.display_name} hat die Einladung angenommen und ist jetzt angemeldet.',
            link=url_for('members.detail', public_id=member.public_id),
        )
    return user


def new_member_officers(exclude=None):
    """Current Fuxmajor(s), as active members; `exclude` is left out (the new member)."""
    office = org.office_by_key(NEW_MEMBER_OFFICE)
    if office is None:
        return []
    return [
        a.member
        for a in org.office_holders(office)
        if a.member.is_active_member and (exclude is None or a.member_id != exclude.id)
    ]


# --- Signed tokens ---------------------------------------------------------------


def fingerprint(value):
    """A short digest that changes with `value` without revealing it; '' for no value."""
    return hashlib.sha256(value.encode()).hexdigest()[:16] if value else ''


def _serializer(salt):
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt=salt)


def _load(token, salt, max_age):
    """The token's payload, or None if it is malformed, forged, for another purpose or too old."""
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return None
    try:
        payload = _serializer(salt).loads(token, max_age=int(max_age.total_seconds()))
    except BadData:
        return None
    return payload if isinstance(payload, dict) else None


def _account(payload):
    """The enabled account a payload names, if its session_version has not changed since."""
    uid, version = payload.get('uid'), payload.get('v')
    if not isinstance(uid, int) or not isinstance(version, int):
        return None
    user = db.session.get(User, uid)
    if user is None or not user.is_enabled or user.session_version != version:
        return None
    return user


def _mail_window(now=None):
    """Number of the current MAIL_WINDOW, part of the mails' dedupe keys."""
    return int((now or utcnow()).timestamp() // MAIL_WINDOW.total_seconds())


def _mail_queued(dedupe_key):
    return db.session.scalar(select(Job.id).where(Job.dedupe_key == dedupe_key)) is not None


# --- Password reset --------------------------------------------------------------


def reset_max_age():
    return timedelta(minutes=current_app.config['PASSWORD_RESET_MAX_AGE_MINUTES'])


def password_reset_token(user):
    payload = {
        'uid': user.id,
        'v': user.session_version,
        'h': fingerprint(user.password_hash),
        'e': fingerprint(user.login_email),
    }
    return _serializer(RESET_SALT).dumps(payload)


def user_from_reset_token(token):
    """The account a reset link is for, or None if the link is invalid, expired or used."""
    payload = _load(token, RESET_SALT, reset_max_age())
    user = _account(payload) if payload else None
    if (
        user is None
        or payload.get('h') != fingerprint(user.password_hash)
        or payload.get('e') != fingerprint(user.login_email)
    ):
        return None
    return user


def request_password_reset(email):
    """ "Passwort vergessen?": mail a reset link if an enabled account uses this login email.

    The caller shows the same message whatever happened, so the form does not reveal which
    addresses have an account. Returns what happened, for the audit log: sent, throttled
    (a link went out in this MAIL_WINDOW already), unknown_email or disabled. Commits nothing.
    """
    email = normalize_email(email)
    user = find_user_by_login_email(email)
    if user is None:
        result = 'unknown_email'
    elif not user.is_enabled:
        result = 'disabled'
    else:
        dedupe_key = f'password_reset:{user.id}:{_mail_window()}'
        if _mail_queued(dedupe_key):
            result = 'throttled'
        else:
            queue_mail(
                user.login_email,
                'password_reset',
                dedupe_key=dedupe_key,
                name=user.member.first_name,
                reset_url=absolute_url('auth.reset_password', token=password_reset_token(user)),
                minutes=current_app.config['PASSWORD_RESET_MAX_AGE_MINUTES'],
            )
            result = 'sent'
    audit.record(
        'auth.password_reset.request',
        entity_type='user',
        entity_id=user.id if user else None,
        diff={'result': result, **({} if user else {'email': email})},
    )
    return result


def reset_password(token, password):
    """Set a new password with a reset link. Returns the user. Commits nothing.

    Raising session_version ends every session of the account and uses the link up. It is a
    conditional UPDATE, so of two simultaneous submits only one gets through. The login lock
    is lifted as well. Raises InvalidLink.
    """
    user = user_from_reset_token(token)
    if user is None:
        raise InvalidLink(INVALID_LINK)
    version = user.session_version
    claimed = db.session.execute(
        update(User)
        .where(User.id == user.id, User.session_version == version)
        .values(session_version=version + 1)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        raise InvalidLink(INVALID_LINK)
    db.session.expire(user, ['session_version'])
    user.set_password(password)
    user.failed_logins = 0
    user.locked_until = None
    audit.record('auth.password_reset', user, actor=user)
    return user


# --- Own password and login email ------------------------------------------------


def verify_current_password(user, password, purpose):
    """Check the current password before a change of password or login email.

    Counted like a login (failed_logins, locked_until), so a stolen session cannot be used to
    guess the password. Commits the attempt counter, and its reset when the password is right
    (also if the change itself is then refused, e.g. for a taken address); on failure, commits
    its audit entry and raises WrongPassword.
    """
    config = current_app.config
    allowed = User.reserve_login_attempt(
        user.id,
        utcnow(),
        max_attempts=config['MAX_FAILED_LOGINS'],
        lockout=timedelta(minutes=config['LOGIN_LOCKOUT_MINUTES']),
    )
    if allowed and user.check_password(password):
        User.clear_failed_logins(user.id)
        return
    reason = 'wrong_password' if allowed else 'locked'
    audit.record('auth.reauth_failed', user, actor=user, diff={'purpose': purpose, 'reason': reason})
    db.session.commit()
    raise WrongPassword(WRONG_PASSWORD if allowed else TOO_MANY_ATTEMPTS)


def change_password(user, current_password, new_password):
    """The logged-in user sets a new password, which ends all their sessions. Returns the user.

    The caller logs the current browser in again after the commit. Raises WrongPassword.
    Commits only what verify_current_password commits.
    """
    verify_current_password(user, current_password, 'password_change')
    user.set_password(new_password)
    end_all_sessions(user)
    audit.record('auth.password_change', user, actor=user)
    return user


def email_change_token(user, new_email):
    payload = {'uid': user.id, 'email': new_email, 'v': user.session_version, 'from': fingerprint(user.login_email)}
    return _serializer(EMAIL_CHANGE_SALT).dumps(payload)


def request_email_change(user, new_email, password):
    """Mail a confirmation link to the new address; the login email changes only when the link
    is used (confirm_email_change). Returns the new address.

    Raises WrongPassword, LoginEmailInUse or AccountError. Commits only what
    verify_current_password commits.
    """
    new_email = normalize_email(new_email)
    verify_current_password(user, password, 'email_change')
    if new_email == user.login_email:
        raise AccountError(SAME_EMAIL)
    if login_email_taken(new_email, user):
        raise LoginEmailInUse(LOGIN_EMAIL_IN_USE)
    dedupe_key = f'email_change:{user.id}:{fingerprint(new_email)}:{_mail_window()}'
    if not _mail_queued(dedupe_key):
        queue_mail(
            new_email,
            'email_change',
            dedupe_key=dedupe_key,
            name=user.member.first_name,
            new_email=new_email,
            confirm_url=absolute_url('auth.confirm_email', token=email_change_token(user, new_email)),
            hours=int(EMAIL_CHANGE_MAX_AGE.total_seconds() // 3600),
        )
    audit.record('auth.email_change.request', user, actor=user, diff={'new_email': new_email})
    return new_email


def email_change_from_token(token):
    """(user, new address) for a confirmation link, or None if it is invalid, expired or used."""
    payload = _load(token, EMAIL_CHANGE_SALT, EMAIL_CHANGE_MAX_AGE)
    user = _account(payload) if payload else None
    if user is None or payload.get('from') != fingerprint(user.login_email):
        return None
    new_email = payload.get('email')
    return (user, new_email) if isinstance(new_email, str) and new_email else None


def confirm_email_change(token):
    """Make the confirmed address the login email and tell the old address. Returns the user.

    Works without a login: the link proves access to the new mailbox, and the password was
    checked when it was requested. Raises InvalidLink, or LoginEmailInUse when someone else
    got the address in the meantime. Commits nothing.
    """
    found = email_change_from_token(token)
    if found is None:
        raise InvalidLink(INVALID_LINK)
    user, new_email = found
    if login_email_taken(new_email, user):
        raise LoginEmailInUse(LOGIN_EMAIL_IN_USE)
    old_email = user.login_email
    try:
        # Conditional, so the same link cannot be used twice at the same moment.
        changed = db.session.execute(
            update(User)
            .where(User.id == user.id, User.login_email == old_email, User.session_version == user.session_version)
            .values(login_email=new_email)
            .execution_options(synchronize_session=False)
        )
    except IntegrityError:  # taken by another account just now (unique login_email)
        raise LoginEmailInUse(LOGIN_EMAIL_IN_USE) from None
    if changed.rowcount != 1:
        raise InvalidLink(INVALID_LINK)
    db.session.expire(user, ['login_email'])
    audit.record('auth.email_change', user, actor=user, diff={'login_email': [old_email, new_email]})
    queue_mail(
        old_email,
        'email_changed',
        name=user.member.first_name,
        old_email=old_email,
        new_email=new_email,
        changed_at=utcnow(),
    )
    return user
