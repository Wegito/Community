from sqlalchemy import select

from community.core.identity.models import Role, User
from community.extensions import db


def register(client, **overrides):
    data = {
        'name': 'Neu',
        'email': 'neu@example.org',
        'invite': 'test-invite',
        'password': 'long enough pw',
        'confirm': 'long enough pw',
    }
    data.update(overrides)
    return client.post('/register', data=data)


def test_register_with_invite_code(client):
    response = register(client)
    assert response.status_code == 302
    user = db.session.scalar(select(User).where(User.email == 'neu@example.org'))
    assert user.role == Role.RESIDENT


def test_register_with_wrong_invite_code(client):
    register(client, invite='falsch')
    assert db.session.scalar(select(User)) is None


def test_registration_closed_without_invite_code(app, client):
    app.config['INVITE_CODE'] = ''
    register(client, invite='')
    register(client, invite='anything')
    assert db.session.scalar(select(User)) is None
