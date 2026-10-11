"""Creating members, their login accounts and the personal invitations that lead from one to the other."""

import hashlib
import secrets
from datetime import timedelta

from flask import current_app
from sqlalchemy import select, update

from community.core.platform import audit
from community.core.time import utcnow
from community.extensions import db

from .models import (
    Invitation,
    InvitationChannel,
    Member,
    MemberStatus,
    User,
    assign_payment_ref,
    normalize_email,
)

# secrets.token_urlsafe(32) gives 43 characters; anything much longer is not one of ours.
MAX_TOKEN_LENGTH = 100


class EmailInUse(Exception):
    """Another account already uses this login email."""


class InvitationError(Exception):
    """An invitation cannot be created or used; the message is shown to the user (German)."""


def create_member(first_name, last_name='', *, email=None, **fields):
    """Create a member (no login). Commits nothing; the caller owns the transaction."""
    member = Member(first_name=first_name.strip(), last_name=(last_name or '').strip(), email=normalize_email(email))
    for key, value in fields.items():
        setattr(member, key, value)
    db.session.add(member)
    db.session.flush()
    assign_payment_ref(member)
    return member


def create_user(member, login_email, password=None):
    """Give a member a login. Raises EmailInUse. Commits nothing."""
    login_email = normalize_email(login_email)
    if db.session.scalar(select(User.id).where(User.login_email == login_email)) is not None:
        raise EmailInUse(login_email)
    user = User(member=member, login_email=login_email)
    if password is not None:
        user.set_password(password)
    db.session.add(user)
    db.session.flush()
    return user


def find_user_by_login_email(email):
    return db.session.scalar(select(User).where(User.login_email == normalize_email(email)))


def end_all_sessions(user):
    """Log the account out on every device. Commits nothing.

    Sessions and remember-me cookies store "id:session_version" (User.get_id); after the
    increment the user loader rejects all of them.
    """
    user.session_version = (user.session_version or 0) + 1
    return user.session_version


# --- Invitations -----------------------------------------------------------------


def hash_token(raw_token):
    """Invitations store only this hash (sha256 hex) of the token in the link."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def invitation_lifetime(channel):
    """QR codes are shown face to face and expire after hours; links and mails after days."""
    if channel == InvitationChannel.QR:
        return timedelta(hours=current_app.config['INVITE_QR_HOURS'])
    return timedelta(days=current_app.config['INVITE_LINK_DAYS'])


def open_invitations(member, now=None):
    """The member's invitations that can still be used, newest first."""
    now = now or utcnow()
    return db.session.scalars(
        select(Invitation)
        .where(
            Invitation.member_id == member.id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
        )
        .order_by(Invitation.created_at.desc(), Invitation.id.desc())
    ).all()


def create_invitation(member, channel, *, created_by, email=None):
    """Invite a member without login. Returns (invitation, raw token). Commits nothing.

    The raw token is returned only here and never stored: show or send it right away.
    The member's other open invitations are revoked, so only the newest link works.
    created_by=None means the server operator (CLI).
    """
    if channel not in InvitationChannel.ALL:
        raise ValueError(f'Unknown invitation channel {channel!r}')
    if member.user is not None:
        raise InvitationError(f'{member.full_name} hat schon einen Login.')
    if member.status != MemberStatus.ACTIVE:
        raise InvitationError(f'{member.full_name} ist kein aktives Mitglied.')
    now = utcnow()
    replaced = open_invitations(member, now)
    for old in replaced:
        old.revoked_at = now
    raw_token = secrets.token_urlsafe(32)
    invitation = Invitation(
        member_id=member.id,
        email=normalize_email(email) or None,
        token_hash=hash_token(raw_token),
        channel=channel,
        expires_at=now + invitation_lifetime(channel),
        created_by_user_id=getattr(created_by, 'id', None),
        created_at=now,
    )
    db.session.add(invitation)
    db.session.flush()
    audit.record(
        'identity.invitation.create',
        invitation,
        actor=created_by,
        diff={
            'member_id': member.id,
            'channel': channel,
            'email': invitation.email,
            'expires_at': invitation.expires_at,
            'revoked_ids': [old.id for old in replaced],
        },
    )
    return invitation, raw_token


def _usable(invitation, now=None):
    member = invitation.member
    return invitation.is_open(now) and member.user is None and member.status == MemberStatus.ACTIVE


def find_valid_invitation(raw_token):
    """The invitation behind a link, or None if it is unknown, expired, revoked or used.

    Also None when the member got a login some other way or has left the club. Callers
    show the same page for every case, so a link reveals nothing about why it fails.
    """
    if not raw_token or len(raw_token) > MAX_TOKEN_LENGTH:
        return None
    invitation = db.session.scalar(select(Invitation).where(Invitation.token_hash == hash_token(raw_token)))
    if invitation is None or not _usable(invitation):
        return None
    return invitation


def accept_invitation(invitation, login_email, password):
    """The invited member creates their login with it. Returns the new User. Commits nothing.

    Raises InvitationError if the invitation can no longer be used, EmailInUse if the login
    email belongs to another account or is another member's contact email.
    """
    now = utcnow()
    if not _usable(invitation, now):
        raise InvitationError('Diese Einladung ist nicht mehr gültig.')
    member = invitation.member
    login_email = normalize_email(login_email)
    # Claim the invitation in one conditional UPDATE: of two simultaneous submits only one
    # matches, and on SQLite it takes the write lock before the email checks below.
    claimed = db.session.execute(
        update(Invitation)
        .where(
            Invitation.id == invitation.id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
        )
        .values(accepted_at=now)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        raise InvitationError('Diese Einladung ist nicht mehr gültig.')
    db.session.expire(invitation, ['accepted_at'])
    other = select(Member.id).where(Member.email == login_email, Member.id != member.id)
    if db.session.scalar(other.limit(1)) is not None:
        raise EmailInUse(login_email)
    user = create_user(member, login_email, password)
    audit.record(
        'identity.invitation.accept',
        invitation,
        actor=user,
        diff={'member_id': member.id, 'user_id': user.id, 'login_email': login_email},
    )
    return user


def revoke_invitation(invitation, *, revoked_by):
    """Make the link unusable. Commits nothing."""
    if invitation.accepted_at is not None:
        raise InvitationError('Diese Einladung wurde schon angenommen.')
    if invitation.revoked_at is not None:
        raise InvitationError('Diese Einladung ist bereits widerrufen.')
    invitation.revoked_at = utcnow()
    audit.record('identity.invitation.revoke', invitation, actor=revoked_by, diff={'member_id': invitation.member_id})
    return invitation


def revoke_open_invitations(member):
    """Revoke every invitation of `member` that could still be used (Austritt). Commits nothing.

    Returns the ids of the revoked invitations; the caller's audit entry names them.
    """
    now = utcnow()
    invitations = open_invitations(member, now)
    for invitation in invitations:
        invitation.revoked_at = now
    return [invitation.id for invitation in invitations]
