"""Creating members and their login accounts."""

from sqlalchemy import select

from community.extensions import db

from .models import Member, User, assign_payment_ref, normalize_email


class EmailInUse(Exception):
    """Another account already uses this login email."""


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
