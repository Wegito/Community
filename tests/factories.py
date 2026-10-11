"""Small helpers to create test data directly in the database."""

from community.core.identity.models import Role, User
from community.extensions import db

DEFAULT_PASSWORD = 'correct horse battery'


def make_user(email='max@example.org', name='Max', password=DEFAULT_PASSWORD, role=Role.RESIDENT):
    user = User(email=email, name=name, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def make_admin(email='admin@example.org', name='Admin', password=DEFAULT_PASSWORD):
    return make_user(email=email, name=name, password=password, role=Role.ADMIN)
