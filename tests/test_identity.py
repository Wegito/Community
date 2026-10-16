from sqlalchemy import select

from community.core.identity.models import Member, User
from community.extensions import db

from .factories import DEFAULT_PASSWORD, make_member, make_user


def test_member_without_login_gets_payment_ref(app):
    member = make_member('Otto Alt', email='Otto@Example.org')
    assert member.user is None
    assert member.payment_ref == f'M-{member.id:04d}'
    assert member.email == 'otto@example.org'
    assert member.public_id and len(member.public_id) == 36


def test_display_name_with_biername(app):
    member = make_member('Max Mustermann', biername='Mäxchen')
    assert member.display_name == 'Max Mustermann (Mäxchen)'


def test_user_belongs_to_member(app):
    user = make_user()
    member = db.session.scalar(select(Member).where(Member.id == user.member_id))
    assert member.user.id == user.id
    assert user.get_id() == f'{user.id}:{user.session_version}'


def test_raising_session_version_logs_out_everywhere(client, login):
    user = make_user()
    login(user)
    assert client.get('/chores').status_code == 200
    user.session_version += 1
    db.session.commit()
    response = client.get('/chores')
    assert response.status_code == 302
    assert '/login' in response.location


def test_disabled_account_cannot_log_in(client):
    user = make_user()
    user.is_enabled = False
    db.session.commit()
    response = client.post('/login', data={'email': user.login_email, 'password': DEFAULT_PASSWORD})
    assert response.status_code == 200
    assert 'Login fehlgeschlagen' in response.get_data(as_text=True)


def test_successful_login_records_time(client):
    user = make_user()
    client.post('/login', data={'email': user.login_email, 'password': DEFAULT_PASSWORD})
    db.session.expire_all()
    assert db.session.get(User, user.id).last_login_at is not None


def test_garbage_session_token_is_ignored(app):
    from community.core.identity import load_user

    assert load_user('abc') is None
    assert load_user('1') is None
    assert load_user('999:1') is None
