from datetime import timedelta

import pytest
from flask_migrate import upgrade
from sqlalchemy import select, text

from community import create_app
from community.config import TestingConfig
from community.core.identity.models import User
from community.core.security import safe_next
from community.core.time import utcnow
from community.extensions import db

from .factories import DEFAULT_PASSWORD, make_user


@pytest.mark.parametrize(
    ('target', 'expected'),
    [
        ('/chores', '/chores'),
        ('/bookings?x=1#top', '/bookings?x=1#top'),
        (None, None),
        ('', None),
        ('chores', None),
        ('//evil.org', None),
        ('/\\evil.org', None),
        ('/\t/evil.org', None),
        ('https://evil.org/', None),
        ('javascript:alert(1)', None),
    ],
)
def test_safe_next(target, expected):
    assert safe_next(target) == expected


def test_home_requires_login(client):
    response = client.get('/')
    assert response.status_code == 302
    assert '/login' in response.location


def test_login_redirects_to_safe_next(client, login):
    make_user()
    response = client.post('/login?next=/chores', data={'email': 'max@example.org', 'password': DEFAULT_PASSWORD})
    assert response.status_code == 302
    assert response.location == '/chores'


def test_login_ignores_unsafe_next(client):
    make_user()
    response = client.post('/login?next=//evil.org/', data={'email': 'max@example.org', 'password': DEFAULT_PASSWORD})
    assert response.location == '/'


def test_login_email_is_case_insensitive(client):
    make_user(email='max@example.org')
    response = client.post('/login', data={'email': '  MAX@Example.ORG ', 'password': DEFAULT_PASSWORD})
    assert response.status_code == 302


def test_wrong_password_and_unknown_email_show_same_message(client):
    make_user()
    wrong = client.post('/login', data={'email': 'max@example.org', 'password': 'wrong password'})
    unknown = client.post('/login', data={'email': 'nobody@example.org', 'password': 'wrong password'})
    assert wrong.status_code == unknown.status_code == 200
    assert 'Login fehlgeschlagen' in wrong.get_data(as_text=True)
    assert 'Login fehlgeschlagen' in unknown.get_data(as_text=True)


def test_account_locks_after_too_many_failures(app, client):
    user = make_user()
    for _ in range(app.config['MAX_FAILED_LOGINS']):
        client.post('/login', data={'email': 'max@example.org', 'password': 'wrong password'})
    db.session.refresh(user)
    assert user.locked_until is not None

    # Even the right password is refused while locked.
    response = client.post('/login', data={'email': 'max@example.org', 'password': DEFAULT_PASSWORD})
    assert response.status_code == 200
    assert 'Login fehlgeschlagen' in response.get_data(as_text=True)

    # After the lock expires, the right password works again and resets the counter.
    user.locked_until = utcnow() - timedelta(seconds=1)
    db.session.commit()
    response = client.post('/login', data={'email': 'max@example.org', 'password': DEFAULT_PASSWORD})
    assert response.status_code == 302
    db.session.refresh(user)
    assert user.failed_logins == 0
    assert user.locked_until is None


def test_remember_me_sets_cookie(client):
    make_user()
    response = client.post('/login', data={'email': 'max@example.org', 'password': DEFAULT_PASSWORD, 'remember': 'y'})
    assert 'remember_token' in response.headers.get('Set-Cookie', '') or any(
        'remember_token' in h for h in response.headers.getlist('Set-Cookie')
    )


def test_logout_needs_post(client, login):
    make_user()
    login('max@example.org')
    assert client.get('/logout').status_code == 405
    response = client.post('/logout')
    assert response.status_code == 302
    assert client.get('/chores').status_code == 302


def test_logout_without_csrf_token_is_rejected(app, client, login):
    make_user()
    login('max@example.org')
    app.config['WTF_CSRF_ENABLED'] = True
    assert client.post('/logout').status_code == 400


def test_register_lowercases_email_and_blocks_case_duplicates(client):
    data = {
        'name': 'Max',
        'email': ' Max@Example.org ',
        'invite': 'test-invite',
        'password': 'long enough pw',
        'confirm': 'long enough pw',
    }
    client.post('/register', data=data)
    assert db.session.scalar(select(User.login_email)) == 'max@example.org'
    client.post('/register', data={**data, 'email': 'MAX@example.org'})
    assert len(db.session.scalars(select(User)).all()) == 1


def test_register_rejects_short_password_with_message(client):
    response = client.post(
        '/register',
        data={
            'name': 'Max',
            'email': 'max@example.org',
            'invite': 'test-invite',
            'password': 'short',
            'confirm': 'short',
        },
    )
    assert 'Mindestens 10 Zeichen' in response.get_data(as_text=True)
    assert db.session.scalar(select(User)) is None


def _app_at_revision(tmp_path, revision):
    class Config(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{tmp_path / "migrate.db"}'

    app = create_app(Config)
    ctx = app.app_context()
    ctx.push()
    upgrade(revision=revision)
    return app, ctx


def _insert_user(email):
    db.session.execute(
        text('INSERT INTO "user" (email, name, password_hash, role) VALUES (:e, :n, :p, :r)'),
        {'e': email, 'n': 'X', 'p': 'x', 'r': 'resident'},
    )
    db.session.commit()


def test_migration_0002_lowercases_emails(tmp_path):
    app, ctx = _app_at_revision(tmp_path, '0001')
    try:
        _insert_user(' Max@Example.org')
        upgrade(revision='0002')
        assert db.session.execute(text('SELECT email FROM "user"')).scalar() == 'max@example.org'
    finally:
        db.session.remove()
        db.engine.dispose()
        ctx.pop()


def test_migration_0002_stops_on_duplicates(tmp_path):
    app, ctx = _app_at_revision(tmp_path, '0001')
    try:
        _insert_user('max@example.org')
        _insert_user('MAX@example.org')
        # Flask-Migrate logs the RuntimeError and exits; nothing may have changed.
        with pytest.raises(SystemExit):
            upgrade(revision='0002')
        emails = db.session.execute(text('SELECT email FROM "user" ORDER BY id')).scalars().all()
        assert emails == ['max@example.org', 'MAX@example.org']
        assert db.session.execute(text('SELECT version_num FROM alembic_version')).scalar() == '0001'
    finally:
        db.session.remove()
        db.engine.dispose()
        ctx.pop()


def test_parallel_wrong_passwords_cannot_exceed_the_limit(app, monkeypatch):
    """Simultaneous attempts are counted atomically: at most MAX_FAILED_LOGINS password checks."""
    import threading

    user = make_user()
    checks = []
    original = User.check_password

    def counting_check(self, pw):
        checks.append(pw)
        return original(self, pw)

    monkeypatch.setattr(User, 'check_password', counting_check)
    attempts = app.config['MAX_FAILED_LOGINS'] * 3
    barrier = threading.Barrier(attempts)

    def attempt():
        client = app.test_client()
        barrier.wait()
        client.post('/login', data={'email': 'max@example.org', 'password': 'wrong password'})

    threads = [threading.Thread(target=attempt) for _ in range(attempts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(checks) <= app.config['MAX_FAILED_LOGINS']
    db.session.refresh(user)
    assert user.locked_until is not None


def test_locked_account_costs_the_same_as_a_wrong_password(app, client, monkeypatch):
    import community.modules.auth.routes as routes

    user = make_user()
    user.locked_until = utcnow() + timedelta(minutes=10)
    db.session.commit()
    calls = []
    original = routes.check_password_hash

    def counting(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(routes, 'check_password_hash', counting)
    response = client.post('/login', data={'email': 'max@example.org', 'password': DEFAULT_PASSWORD})
    assert response.status_code == 200
    assert len(calls) == 1
