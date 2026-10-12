"""M1-8: "Passwort vergessen?", changing the own password and the login email."""

import re
import time
from datetime import timedelta

import pytest
from itsdangerous import TimestampSigner, URLSafeTimedSerializer
from sqlalchemy import select, update

from community.core.identity.models import User
from community.core.identity.services import end_all_sessions
from community.core.platform.audit import AuditLog
from community.core.platform.jobs import Job, run_due_jobs
from community.core.time import utcnow
from community.extensions import db
from community.modules.auth import services
from community.modules.auth.services import (
    LOGIN_EMAIL_IN_USE,
    SAME_EMAIL,
    TOO_MANY_ATTEMPTS,
    WRONG_PASSWORD,
    AccountError,
    InvalidLink,
    LoginEmailInUse,
    WrongPassword,
    change_password,
    confirm_email_change,
    email_change_from_token,
    email_change_token,
    password_reset_token,
    request_email_change,
    request_password_reset,
    reset_password,
    user_from_reset_token,
)

from .factories import DEFAULT_PASSWORD, make_member, make_user

NEW_PASSWORD = 'ein ganz neues passwort'
RESET_LINK = re.compile(r'http://localhost/password/reset/([A-Za-z0-9_.-]+)')
CONFIRM_LINK = re.compile(r'http://localhost/settings/email/confirm/([A-Za-z0-9_.-]+)')
INVALID = 'Link ungültig oder abgelaufen – bitte einen neuen anfordern.'


def audit_entries(action):
    return db.session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)).all()


def mails(to=None):
    """Queued mails (job payloads), oldest first."""
    db.session.expire_all()
    jobs = db.session.scalars(select(Job).where(Job.kind == 'mail.send').order_by(Job.id)).all()
    return [job.payload for job in jobs if to is None or job.payload['to'] == to]


def reload(obj):
    db.session.expire_all()
    return db.session.get(type(obj), obj.id)


def sign_in(client, email, password=DEFAULT_PASSWORD, remember=False):
    data = {'email': email, 'password': password}
    if remember:
        data['remember'] = 'y'
    response = client.post('/login', data=data)
    assert response.status_code == 302, 'login failed'
    return client


def is_logged_in(client):
    """A page that only needs a login: 200 when logged in, else a redirect to /login."""
    response = client.get('/profile')
    assert response.status_code in (200, 302)
    return response.status_code == 200


def only_remember_cookie(client):
    """Drop the session cookie, so only "Angemeldet bleiben" can log this client in."""
    client.delete_cookie('session')
    return client


def later(monkeypatch, seconds):
    """Check tokens as if `seconds` had passed since they were made."""
    monkeypatch.setattr(TimestampSigner, 'get_timestamp', lambda self: int(time.time()) + seconds)


def forgot(client, email):
    return client.post('/password/forgot', data={'email': email})


def reset_form(password=NEW_PASSWORD, confirm=None):
    return {'password': password, 'confirm': password if confirm is None else confirm}


# --- Reset tokens ----------------------------------------------------------------


def test_reset_token_names_the_account(app):
    user = make_user()
    assert user_from_reset_token(password_reset_token(user)) == user


def test_forged_and_foreign_tokens_are_rejected(app):
    user = make_user()
    payload = {'uid': user.id, 'v': user.session_version, 'h': services.fingerprint(user.password_hash)}
    own_key = app.config['SECRET_KEY']
    for token in (
        '',
        'not-a-token',
        password_reset_token(user)[:-2] + 'xx',  # signature broken
        'x' * 2000,
        URLSafeTimedSerializer('another-key', salt='password-reset').dumps(payload),
        URLSafeTimedSerializer(own_key, salt='password-reset').dumps(['not', 'a', 'dict']),
        URLSafeTimedSerializer(own_key, salt='password-reset').dumps({**payload, 'uid': str(user.id)}),
        email_change_token(user, 'neu@example.org'),  # right key, other purpose
    ):
        assert user_from_reset_token(token) is None
    assert email_change_from_token(password_reset_token(user)) is None


def test_the_token_does_not_contain_the_password_hash(app):
    user = make_user()
    payload = URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='password-reset').loads(password_reset_token(user))
    assert payload['h'] and payload['h'] not in user.password_hash
    assert payload['e'] and 'max' not in payload['e']
    assert set(payload) == {'uid', 'v', 'h', 'e'}


@pytest.mark.parametrize('change', ['password', 'sessions', 'disabled', 'no_password', 'login_email'])
def test_reset_token_dies_after_password_or_session_changes(app, change):
    user = make_user()
    token = password_reset_token(user)
    if change == 'password':
        user.set_password('anders gesetzt, z. B. per create-admin')  # without a version change
    elif change == 'sessions':
        end_all_sessions(user)
    elif change == 'disabled':
        user.is_enabled = False
    elif change == 'login_email':
        user.login_email = 'neu@example.org'
    else:
        user.password_hash = None
    db.session.commit()
    assert user_from_reset_token(token) is None


def test_reset_link_to_the_old_address_dies_with_the_email_change(app):
    """Moving the login away from a mailbox also takes the reset links sent there (finding 7)."""
    user = make_user()
    token, version = password_reset_token(user), user.session_version
    with app.test_request_context():
        confirm_email_change(email_change_token(user, 'neu@example.org'))
    db.session.commit()
    assert reload(user).session_version == version  # nobody is logged out (decision 43)
    with pytest.raises(InvalidLink):
        reset_password(token, NEW_PASSWORD)
    db.session.rollback()
    assert reload(user).check_password(DEFAULT_PASSWORD)


def test_reset_token_works_for_an_account_without_password(app):
    user = make_user()
    user.password_hash = None
    db.session.commit()
    assert user_from_reset_token(password_reset_token(user)) == user


def test_reset_token_expires(app, monkeypatch):
    user = make_user()
    token = password_reset_token(user)
    later(monkeypatch, 59 * 60)
    assert user_from_reset_token(token) == user
    later(monkeypatch, 60 * 60 + 1)
    assert user_from_reset_token(token) is None
    app.config['PASSWORD_RESET_MAX_AGE_MINUTES'] = 120
    assert user_from_reset_token(token) == user


# --- Requesting a reset (service) ------------------------------------------------


def test_request_password_reset_queues_the_mandatory_mail(app):
    user = make_user()
    user.member.first_name = 'Max'
    with app.test_request_context():
        assert request_password_reset(' Max@Example.org ') == 'sent'
    db.session.commit()
    (mail,) = mails()
    assert mail['to'] == 'max@example.org'
    assert mail['subject'] == 'Passwort zurücksetzen'
    assert mail['body'].startswith('Hallo Max,')
    assert '60 Minuten' in mail['body']
    assert 'Welche Nachrichten du bekommst' not in mail['body']  # mandatory: no settings link
    token = RESET_LINK.search(mail['body']).group(1)
    assert user_from_reset_token(token) == user
    (entry,) = audit_entries('auth.password_reset.request')
    assert entry.entity_id == str(user.id) and entry.diff == {'result': 'sent'} and entry.actor_user_id is None


def test_reset_link_uses_base_url(app):
    app.config['BASE_URL'] = 'https://community.example.org'
    make_user()
    with app.test_request_context(base_url='http://evil.example.com'):
        request_password_reset('max@example.org')
    db.session.commit()
    assert 'https://community.example.org/password/reset/' in mails()[0]['body']
    assert 'evil.example.com' not in mails()[0]['body']


def test_no_mail_for_unknown_or_disabled_accounts(app):
    disabled = make_user(email='weg@example.org', name='Gesperrt Person')
    disabled.is_enabled = False
    db.session.commit()
    with app.test_request_context():
        assert request_password_reset('nobody@example.org') == 'unknown_email'
        assert request_password_reset('weg@example.org') == 'disabled'
    db.session.commit()
    assert mails() == []
    unknown, locked = audit_entries('auth.password_reset.request')
    assert unknown.entity_id is None and unknown.diff == {'result': 'unknown_email', 'email': 'nobody@example.org'}
    assert locked.entity_id == str(disabled.id) and locked.diff == {'result': 'disabled'}


def test_reset_mails_are_limited_per_account_and_window(app, monkeypatch):
    make_user()
    make_user(email='other@example.org', name='Other Person')
    now = utcnow()
    monkeypatch.setattr(services, 'utcnow', lambda: now)
    with app.test_request_context():
        assert request_password_reset('max@example.org') == 'sent'
        db.session.commit()
        assert request_password_reset('max@example.org') == 'throttled'
        assert request_password_reset('other@example.org') == 'sent'
        db.session.commit()
        monkeypatch.setattr(services, 'utcnow', lambda: now + services.MAIL_WINDOW)
        assert request_password_reset('max@example.org') == 'sent'
        db.session.commit()
    assert len(mails('max@example.org')) == 2 and len(mails('other@example.org')) == 1
    results = [e.diff['result'] for e in audit_entries('auth.password_reset.request')]
    assert results == ['sent', 'throttled', 'sent', 'sent']


# --- Resetting (service) ---------------------------------------------------------


def test_reset_password_sets_it_ends_sessions_and_unlocks(app):
    user = make_user()
    user.failed_logins = 3
    user.locked_until = utcnow() + timedelta(minutes=10)
    db.session.commit()
    version = user.session_version
    token = password_reset_token(user)

    reset_password(token, NEW_PASSWORD)
    db.session.commit()
    user = reload(user)
    assert user.check_password(NEW_PASSWORD) and not user.check_password(DEFAULT_PASSWORD)
    assert user.session_version == version + 1
    assert user.failed_logins == 0 and user.locked_until is None
    (entry,) = audit_entries('auth.password_reset')
    assert entry.actor_user_id == user.id and entry.entity_id == str(user.id)

    with pytest.raises(InvalidLink):  # single use
        reset_password(token, 'noch ein anderes passwort')
    assert reload(user).check_password(NEW_PASSWORD)


def test_concurrent_reset_is_stopped_by_the_conditional_update(app):
    """Two submits of one link: the second request still holds the old session_version, but the
    conditional UPDATE matches nothing once the first one has raised it."""
    user = make_user()
    token = password_reset_token(user)
    version = user.session_version
    db.session.execute(
        update(User)
        .where(User.id == user.id)
        .values(session_version=User.session_version + 1)
        .execution_options(synchronize_session=False)
    )
    assert user.session_version == version  # the stale object still looks valid
    with pytest.raises(InvalidLink):
        reset_password(token, NEW_PASSWORD)
    db.session.rollback()
    assert reload(user).check_password(DEFAULT_PASSWORD)


# --- Changing the own password (service) -----------------------------------------


def test_change_password_checks_the_current_one(app):
    user = make_user()
    version = user.session_version
    with pytest.raises(WrongPassword, match=WRONG_PASSWORD):
        change_password(user, 'falsch geraten', NEW_PASSWORD)
    db.session.rollback()
    user = reload(user)
    assert user.check_password(DEFAULT_PASSWORD) and user.session_version == version
    assert user.failed_logins == 1
    (failed,) = audit_entries('auth.reauth_failed')
    assert failed.diff == {'purpose': 'password_change', 'reason': 'wrong_password'}

    change_password(user, DEFAULT_PASSWORD, NEW_PASSWORD)
    db.session.commit()
    user = reload(user)
    assert user.check_password(NEW_PASSWORD)
    assert user.session_version == version + 1 and user.failed_logins == 0
    assert [e.actor_user_id for e in audit_entries('auth.password_change')] == [user.id]


def test_guessing_the_current_password_locks_like_the_login(app):
    user = make_user()
    for _ in range(app.config['MAX_FAILED_LOGINS']):
        with pytest.raises(WrongPassword, match=WRONG_PASSWORD):
            change_password(user, 'falsch geraten', NEW_PASSWORD)
    with pytest.raises(WrongPassword, match=TOO_MANY_ATTEMPTS):
        change_password(user, DEFAULT_PASSWORD, NEW_PASSWORD)
    assert reload(user).check_password(DEFAULT_PASSWORD)
    assert audit_entries('auth.reauth_failed')[-1].diff['reason'] == 'locked'


# --- Changing the login email (service) ------------------------------------------


def test_request_email_change_mails_the_new_address(app):
    user = make_user()
    with app.test_request_context():
        assert request_email_change(user, ' Neu@Example.org ', DEFAULT_PASSWORD) == 'neu@example.org'
    db.session.commit()
    assert reload(user).login_email == 'max@example.org'  # not before the confirmation
    (mail,) = mails()
    assert mail['to'] == 'neu@example.org'
    assert mail['subject'] == 'Neue Login-E-Mail-Adresse bestätigen'
    assert '24 Stunden' in mail['body']
    token = CONFIRM_LINK.search(mail['body']).group(1)
    assert email_change_from_token(token) == (user, 'neu@example.org')
    (entry,) = audit_entries('auth.email_change.request')
    assert entry.diff == {'new_email': 'neu@example.org'} and entry.actor_user_id == user.id


def test_request_email_change_rules(app):
    user = make_user()
    make_user(email='login@example.org', name='Login Person')
    make_member('Kontakt Person', email='kontakt@example.org')
    user.member.email = 'eigene@example.org'
    db.session.commit()
    with app.test_request_context():
        with pytest.raises(WrongPassword):
            request_email_change(user, 'neu@example.org', 'falsch geraten')
        with pytest.raises(AccountError, match=SAME_EMAIL):
            request_email_change(user, 'MAX@example.org', DEFAULT_PASSWORD)
        for taken in ('login@example.org', 'kontakt@example.org'):
            with pytest.raises(LoginEmailInUse, match=LOGIN_EMAIL_IN_USE):
                request_email_change(user, taken, DEFAULT_PASSWORD)
        db.session.rollback()
        assert mails() == []
        # The member's own contact email is fine.
        request_email_change(user, 'eigene@example.org', DEFAULT_PASSWORD)


def test_a_refused_address_with_the_right_password_does_not_lock(app):
    """Only wrong passwords count; the right one clears the counter, also when the address is refused (finding 8)."""
    user = make_user()
    make_member('Kontakt Person', email='kontakt@example.org')
    with app.test_request_context():
        with pytest.raises(WrongPassword, match=WRONG_PASSWORD):
            request_email_change(user, 'neu@example.org', 'falsch geraten')
        db.session.rollback()
        for _ in range(app.config['MAX_FAILED_LOGINS']):
            with pytest.raises(LoginEmailInUse):
                request_email_change(user, 'kontakt@example.org', DEFAULT_PASSWORD)
            db.session.rollback()  # as the route does
            with pytest.raises(AccountError, match=SAME_EMAIL):
                request_email_change(user, 'max@example.org', DEFAULT_PASSWORD)
            db.session.rollback()
    user = reload(user)
    assert (user.failed_logins, user.locked_until) == (0, None)


def test_confirm_email_change_switches_and_tells_the_old_address(app):
    user = make_user()
    user.member.first_name = 'Max'
    db.session.commit()
    token = email_change_token(user, 'neu@example.org')
    version = user.session_version
    with app.test_request_context():
        confirm_email_change(token)
    db.session.commit()
    user = reload(user)
    assert user.login_email == 'neu@example.org'
    assert user.session_version == version  # no logout
    (mail,) = mails()
    assert mail['to'] == 'max@example.org'
    assert mail['subject'] == 'Login-E-Mail-Adresse geändert'
    assert mail['body'].startswith('Hallo Max,')
    assert 'Neu:    neu@example.org' in mail['body'] and 'Bisher: max@example.org' in mail['body']
    (entry,) = audit_entries('auth.email_change')
    assert entry.diff == {'login_email': ['max@example.org', 'neu@example.org']}
    assert entry.actor_user_id == user.id

    with pytest.raises(InvalidLink):  # single use
        confirm_email_change(token)


@pytest.mark.parametrize('taken_by', ['login', 'contact'])
def test_confirm_refuses_an_address_taken_in_the_meantime(app, taken_by):
    user = make_user()
    token = email_change_token(user, 'neu@example.org')
    if taken_by == 'login':
        make_user(email='neu@example.org', name='Schneller Person')
    else:
        make_member('Kontakt Person', email='neu@example.org')
    with pytest.raises(LoginEmailInUse):
        confirm_email_change(token)
    db.session.rollback()
    assert reload(user).login_email == 'max@example.org'
    assert mails() == []


def test_email_change_token_dies_after_a_password_change_and_expires(app, monkeypatch):
    user = make_user()
    token = email_change_token(user, 'neu@example.org')
    later(monkeypatch, 24 * 3600 - 60)
    assert email_change_from_token(token) == (user, 'neu@example.org')
    later(monkeypatch, 24 * 3600 + 1)
    assert email_change_from_token(token) is None
    monkeypatch.undo()
    change_password(user, DEFAULT_PASSWORD, NEW_PASSWORD)
    db.session.commit()
    assert email_change_from_token(token) is None


# --- Pages: "Passwort vergessen?" ------------------------------------------------


def test_login_page_links_to_forgot_password(client):
    page = client.get('/login').get_data(as_text=True)
    assert 'href="/password/forgot"' in page and 'Passwort vergessen?' in page
    assert client.get('/password/forgot').status_code == 200


def test_forgot_password_answers_the_same_for_every_address(client):
    make_user()
    disabled = make_user(email='weg@example.org', name='Gesperrt Person')
    disabled.is_enabled = False
    db.session.commit()

    responses = [forgot(client, email) for email in ('max@example.org', 'nobody@example.org', 'weg@example.org')]
    assert {r.status_code for r in responses} == {200}
    bodies = {r.get_data(as_text=True) for r in responses}
    assert len(bodies) == 1
    assert 'Wenn es ein Konto mit dieser E-Mail-Adresse gibt' in bodies.pop()
    assert [m['to'] for m in mails()] == ['max@example.org']
    results = [e.diff['result'] for e in audit_entries('auth.password_reset.request')]
    assert results == ['sent', 'unknown_email', 'disabled']


def test_forgot_password_repeated_quickly_sends_one_mail(client):
    make_user()
    first, second = forgot(client, 'max@example.org'), forgot(client, 'max@example.org')
    assert first.get_data() == second.get_data()
    assert len(mails()) == 1


def test_forgot_password_needs_a_valid_address(client):
    response = forgot(client, 'kein-email')
    assert response.status_code == 200
    assert 'Wenn es ein Konto' not in response.get_data(as_text=True)
    assert mails() == [] and audit_entries('auth.password_reset.request') == []


def test_reset_by_mail_link_ends_all_sessions(app, client):
    user = make_user()
    other_device = sign_in(app.test_client(), 'max@example.org')
    remembered = only_remember_cookie(sign_in(app.test_client(), 'max@example.org', remember=True))
    assert is_logged_in(other_device) and is_logged_in(only_remember_cookie(remembered))

    forgot(client, 'max@example.org')
    assert run_due_jobs()['done'] == 1
    mail = app.extensions['mail_outbox'][-1]
    token = RESET_LINK.search(mail['body']).group(1)

    page = client.get(f'/password/reset/{token}')
    assert page.status_code == 200
    assert page.headers['Cache-Control'] == 'no-store' and page.headers['Referrer-Policy'] == 'no-referrer'
    assert 'max@example.org' in page.get_data(as_text=True)

    response = client.post(f'/password/reset/{token}', data=reset_form())
    assert response.status_code == 302 and response.location.endswith('/login')
    assert 'Dein Passwort ist geändert' in client.get('/login').get_data(as_text=True)
    assert not is_logged_in(other_device)
    assert not is_logged_in(only_remember_cookie(remembered))
    assert reload(user).check_password(NEW_PASSWORD)

    # Single use: the link now gets the friendly "invalid" page.
    used = client.get(f'/password/reset/{token}')
    assert used.status_code == 404 and INVALID in used.get_data(as_text=True)
    assert client.post(f'/password/reset/{token}', data=reset_form('noch ein anderes')).status_code == 404

    fresh = app.test_client()
    assert fresh.post('/login', data={'email': 'max@example.org', 'password': DEFAULT_PASSWORD}).status_code == 200
    assert is_logged_in(sign_in(fresh, 'max@example.org', NEW_PASSWORD))


def test_reset_form_errors_keep_the_link_usable(client):
    user = make_user()
    token = password_reset_token(user)
    short = client.post(f'/password/reset/{token}', data=reset_form('kurz'))
    assert 'Mindestens 10 und höchstens 256 Zeichen' in short.get_data(as_text=True)
    mismatch = client.post(f'/password/reset/{token}', data=reset_form(confirm='etwas ganz anderes'))
    assert 'stimmen nicht überein' in mismatch.get_data(as_text=True)
    assert reload(user).check_password(DEFAULT_PASSWORD)
    assert client.get(f'/password/reset/{token}').status_code == 200


def test_expired_reset_link_gets_the_friendly_page(client, monkeypatch):
    user = make_user()
    token = password_reset_token(user)
    later(monkeypatch, 61 * 60)
    response = client.get(f'/password/reset/{token}')
    assert response.status_code == 404
    page = response.get_data(as_text=True)
    assert INVALID in page and 'href="/password/forgot"' in page
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert client.post(f'/password/reset/{token}', data=reset_form()).status_code == 404
    assert reload(user).check_password(DEFAULT_PASSWORD)


def test_reset_unlocks_a_locked_account(app, client):
    user = make_user()
    for _ in range(app.config['MAX_FAILED_LOGINS']):
        client.post('/login', data={'email': 'max@example.org', 'password': 'falsch geraten'})
    assert reload(user).locked_until is not None
    client.post(f'/password/reset/{password_reset_token(reload(user))}', data=reset_form())
    assert is_logged_in(sign_in(app.test_client(), 'max@example.org', NEW_PASSWORD))


def test_reset_while_logged_in_logs_this_browser_out(client, login):
    user = make_user()
    other = make_user(email='other@example.org', name='Other Person')
    login(other)
    response = client.post(f'/password/reset/{password_reset_token(user)}', data=reset_form())
    assert response.status_code == 302 and response.location.endswith('/login')
    assert not is_logged_in(client)
    assert reload(user).check_password(NEW_PASSWORD) and reload(other).check_password(DEFAULT_PASSWORD)


# --- Pages: own password ---------------------------------------------------------


@pytest.mark.parametrize('path', ['/settings/password', '/settings/email'])
def test_settings_need_a_login(client, path):
    make_user()
    response = client.post(path, data={'current_password': DEFAULT_PASSWORD, 'password': DEFAULT_PASSWORD})
    assert response.status_code == 302 and '/login' in response.location


def test_profile_links_to_password_and_email_settings(client, login):
    login(make_user(perms=[]))
    page = client.get('/profile').get_data(as_text=True)
    assert 'href="/settings/password"' in page and 'href="/settings/email"' in page


def change_form(current=DEFAULT_PASSWORD, password=NEW_PASSWORD, confirm=None):
    return {'current_password': current, **reset_form(password, confirm)}


def test_change_password_keeps_this_browser_and_ends_the_others(app, client):
    user = make_user()
    other_device = sign_in(app.test_client(), 'max@example.org')
    other_remembered = sign_in(app.test_client(), 'max@example.org', remember=True)
    sign_in(client, 'max@example.org', remember=True)

    response = client.post('/settings/password', data=change_form())
    assert response.status_code == 302 and response.location.endswith('/profile')
    assert 'Passwort geändert' in client.get('/profile').get_data(as_text=True)
    assert is_logged_in(client)
    # This browser also got a new "Angemeldet bleiben" cookie for the new session_version.
    assert is_logged_in(only_remember_cookie(client))
    assert not is_logged_in(other_device)
    assert not is_logged_in(only_remember_cookie(other_remembered))
    assert reload(user).check_password(NEW_PASSWORD)
    assert is_logged_in(sign_in(app.test_client(), 'max@example.org', NEW_PASSWORD))


def test_change_password_without_remember_me_sets_no_cookie(client):
    make_user()
    sign_in(client, 'max@example.org')
    client.post('/settings/password', data=change_form())
    assert is_logged_in(client)
    assert client.get_cookie('remember_token') is None


def test_change_password_with_wrong_current_password(client):
    user = make_user()
    sign_in(client, 'max@example.org')
    response = client.post('/settings/password', data=change_form(current='falsch geraten'))
    assert response.status_code == 200
    assert WRONG_PASSWORD in response.get_data(as_text=True)
    assert reload(user).check_password(DEFAULT_PASSWORD)
    assert is_logged_in(client)


def test_change_password_validates_the_new_one(client):
    user = make_user()
    sign_in(client, 'max@example.org')
    short = client.post('/settings/password', data=change_form(password='kurz'))
    assert 'Mindestens 10 und höchstens 256 Zeichen' in short.get_data(as_text=True)
    mismatch = client.post('/settings/password', data=change_form(confirm='etwas ganz anderes'))
    assert 'stimmen nicht überein' in mismatch.get_data(as_text=True)
    assert reload(user).check_password(DEFAULT_PASSWORD) and reload(user).failed_logins == 0


# --- Pages: own login email ------------------------------------------------------


def email_form(login_email='neu@example.org', password=DEFAULT_PASSWORD):
    return {'login_email': login_email, 'password': password}


def test_change_email_by_mail_link(app, client):
    user = make_user()
    sign_in(client, 'max@example.org')
    response = client.post('/settings/email', data=email_form(login_email=' Neu@Example.org '))
    assert response.status_code == 302 and response.location.endswith('/profile')
    assert 'Bestätigungslink an neu@example.org' in client.get('/profile').get_data(as_text=True)
    assert reload(user).login_email == 'max@example.org'

    assert run_due_jobs()['done'] == 1
    token = CONFIRM_LINK.search(app.extensions['mail_outbox'][-1]['body']).group(1)
    client.post('/logout')

    # Logged out: GET only shows the button and changes nothing.
    page = client.get(f'/settings/email/confirm/{token}')
    assert page.status_code == 200 and 'neu@example.org' in page.get_data(as_text=True)
    assert reload(user).login_email == 'max@example.org'

    response = client.post(f'/settings/email/confirm/{token}')
    assert response.status_code == 302 and response.location.endswith('/login')
    assert 'Deine Login-E-Mail-Adresse ist jetzt neu@example.org' in client.get('/login').get_data(as_text=True)
    assert reload(user).login_email == 'neu@example.org'
    assert [m['subject'] for m in mails('max@example.org')] == ['Login-E-Mail-Adresse geändert']

    assert is_logged_in(sign_in(app.test_client(), 'neu@example.org'))
    old = app.test_client().post('/login', data={'email': 'max@example.org', 'password': DEFAULT_PASSWORD})
    assert old.status_code == 200 and 'Login fehlgeschlagen' in old.get_data(as_text=True)

    used = client.get(f'/settings/email/confirm/{token}')
    assert used.status_code == 404 and INVALID in used.get_data(as_text=True)


def test_confirm_while_logged_in_keeps_the_session(client):
    user = make_user()
    sign_in(client, 'max@example.org')
    response = client.post(f'/settings/email/confirm/{email_change_token(user, "neu@example.org")}')
    assert response.status_code == 302 and response.location.endswith('/profile')
    assert is_logged_in(client)
    assert 'neu@example.org' in client.get('/profile').get_data(as_text=True)


def test_confirm_link_changes_only_its_own_account(client):
    owner = make_user()
    other = make_user(email='other@example.org', name='Other Person')
    sign_in(client, 'other@example.org')
    client.post(f'/settings/email/confirm/{email_change_token(owner, "neu@example.org")}')
    assert reload(owner).login_email == 'neu@example.org'
    assert reload(other).login_email == 'other@example.org'


def test_change_email_errors(client):
    user = make_user()
    make_user(email='taken@example.org', name='Taken Person')
    sign_in(client, 'max@example.org')
    wrong = client.post('/settings/email', data=email_form(password='falsch geraten'))
    assert wrong.status_code == 200 and WRONG_PASSWORD in wrong.get_data(as_text=True)
    taken = client.post('/settings/email', data=email_form(login_email='Taken@example.org'))
    assert LOGIN_EMAIL_IN_USE in taken.get_data(as_text=True)
    same = client.post('/settings/email', data=email_form(login_email='max@example.org'))
    assert SAME_EMAIL in same.get_data(as_text=True)
    invalid = client.post('/settings/email', data=email_form(login_email='kein-email'))
    assert invalid.status_code == 200
    assert mails() == [] and reload(user).login_email == 'max@example.org'


def test_refused_addresses_do_not_lock_the_login(app, client):
    make_user()
    make_user(email='taken@example.org', name='Taken Person')
    sign_in(client, 'max@example.org')
    for _ in range(app.config['MAX_FAILED_LOGINS']):
        refused = client.post('/settings/email', data=email_form(login_email='taken@example.org'))
        assert LOGIN_EMAIL_IN_USE in refused.get_data(as_text=True)
    assert is_logged_in(sign_in(app.test_client(), 'max@example.org'))
    assert mails() == []


def test_confirm_with_an_address_taken_meanwhile(client):
    user = make_user()
    token = email_change_token(user, 'neu@example.org')
    make_user(email='neu@example.org', name='Schneller Person')
    response = client.post(f'/settings/email/confirm/{token}')
    assert response.status_code == 409
    assert LOGIN_EMAIL_IN_USE in response.get_data(as_text=True)
    assert reload(user).login_email == 'max@example.org'
