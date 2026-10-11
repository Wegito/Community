"""Small helpers to create test data directly in the database."""

from community.core.identity.models import Role, split_name
from community.core.identity.services import create_member, create_user
from community.extensions import db

DEFAULT_PASSWORD = 'correct horse battery'


def make_member(name='Max Mustermann', email=None, **fields):
    first, last = split_name(name)
    member = create_member(first, last, email=email, **fields)
    db.session.commit()
    return member


def make_user(email='max@example.org', name='Max Mustermann', password=DEFAULT_PASSWORD, role=Role.RESIDENT):
    first, last = split_name(name)
    member = create_member(first, last, email=email)
    user = create_user(member, email, password=password)
    user.role = role
    db.session.commit()
    return user


def make_admin(email='admin@example.org', name='Admin Istrator', password=DEFAULT_PASSWORD):
    return make_user(email=email, name=name, password=password, role=Role.ADMIN)
