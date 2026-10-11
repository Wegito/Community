from sqlalchemy import select

from community.core.authz.models import SYSADMIN
from community.core.authz.service import active_roles, permissions_for
from community.core.identity.cli import create_admin
from community.core.identity.models import User
from community.extensions import db

from .factories import make_manager, make_user


def role_keys(user):
    return {role.key for role in active_roles(user.member)}


def test_create_admin_command_creates_admin(app):
    result = app.test_cli_runner().invoke(
        create_admin, ['--email', ' Boss@Example.org ', '--name', 'Boss', '--password', 'long enough pw']
    )
    assert result.exit_code == 0, result.output
    user = db.session.scalar(select(User).where(User.login_email == 'boss@example.org'))
    assert role_keys(user) == {SYSADMIN}
    assert 'system.admin' in permissions_for(user.member)
    assert user.check_password('long enough pw')


def test_create_admin_command_promotes_existing_user(app):
    make_user(email='max@example.org')
    result = app.test_cli_runner().invoke(
        create_admin, ['--email', 'max@example.org', '--name', 'ignored', '--password', 'new password!']
    )
    assert result.exit_code == 0, result.output
    user = db.session.scalar(select(User).where(User.login_email == 'max@example.org'))
    db.session.refresh(user)
    assert SYSADMIN in role_keys(user)
    # Running it again does not add the role twice.
    app.test_cli_runner().invoke(
        create_admin, ['--email', 'max@example.org', '--name', 'ignored', '--password', 'new password!']
    )
    from community.core.authz.models import MemberRole

    assert len(db.session.scalars(select(MemberRole).where(MemberRole.member_id == user.member_id)).all()) == 1


def test_non_admin_cannot_create_announcement(client, login):
    make_user()
    login('max@example.org')
    response = client.post('/announcements/new', data={'title': 'Unerlaubt', 'body': 'x'})
    assert response.status_code == 403
    assert client.get('/announcements').get_data(as_text=True).count('Unerlaubt') == 0


def test_manager_can_create_announcement(client, login):
    make_manager()
    login('chef@example.org')
    response = client.post('/announcements/new', data={'title': 'Kneipe', 'body': 'Freitag 20 Uhr'})
    assert response.status_code == 302
    assert 'Kneipe' in client.get('/announcements').get_data(as_text=True)


def test_create_admin_rejects_short_password_and_bad_email(app):
    runner = app.test_cli_runner()
    short = runner.invoke(create_admin, ['--email', 'boss@example.org', '--name', 'Boss', '--password', 'short'])
    assert short.exit_code != 0
    bad_email = runner.invoke(
        create_admin, ['--email', 'not-an-email', '--name', 'Boss', '--password', 'long enough pw']
    )
    assert bad_email.exit_code != 0
    assert db.session.scalar(select(User)) is None
