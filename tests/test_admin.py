from sqlalchemy import select

from community.core.identity.cli import create_admin
from community.core.identity.models import Role, User
from community.extensions import db

from .factories import make_admin, make_user


def test_create_admin_command_creates_admin(app):
    result = app.test_cli_runner().invoke(
        create_admin, ['--email', ' Boss@Example.org ', '--name', 'Boss', '--password', 'long enough pw']
    )
    assert result.exit_code == 0, result.output
    user = db.session.scalar(select(User).where(User.email == 'boss@example.org'))
    assert user.role == Role.ADMIN
    assert user.check_password('long enough pw')


def test_create_admin_command_promotes_existing_user(app):
    make_user(email='max@example.org')
    result = app.test_cli_runner().invoke(
        create_admin, ['--email', 'max@example.org', '--name', 'ignored', '--password', 'new password!']
    )
    assert result.exit_code == 0, result.output
    user = db.session.scalar(select(User).where(User.email == 'max@example.org'))
    db.session.refresh(user)
    assert user.role == Role.ADMIN


def test_non_admin_cannot_create_announcement(client, login):
    make_user()
    login('max@example.org')
    response = client.post('/announcements/new', data={'title': 'Unerlaubt', 'body': 'x'})
    assert response.status_code == 302
    assert client.get('/announcements').get_data(as_text=True).count('Unerlaubt') == 0


def test_admin_can_create_announcement(client, login):
    make_admin()
    login('admin@example.org')
    response = client.post('/announcements/new', data={'title': 'Kneipe', 'body': 'Freitag 20 Uhr'})
    assert response.status_code == 302
    assert 'Kneipe' in client.get('/announcements').get_data(as_text=True)
