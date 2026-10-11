from datetime import timedelta

from sqlalchemy import select

from community.core.platform.audit import AuditLog, purge_ips
from community.core.time import utcnow
from community.extensions import db
from community.modules.tasks.models import Chore

from .factories import DEFAULT_PASSWORD, make_admin, make_user


def actions():
    return [e.action for e in db.session.scalars(select(AuditLog).order_by(AuditLog.id))]


def test_login_success_and_failure_are_recorded(client):
    user = make_user()
    client.post('/login', data={'email': user.login_email, 'password': 'wrong password'})
    client.post('/login', data={'email': 'nobody@example.org', 'password': 'wrong password'})
    client.post('/login', data={'email': user.login_email, 'password': DEFAULT_PASSWORD})
    entries = db.session.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    assert [e.action for e in entries] == ['auth.login_failed', 'auth.login_failed', 'auth.login']
    assert entries[0].diff == {'reason': 'wrong_password'}
    assert entries[1].diff == {'reason': 'unknown_email', 'email': 'nobody@example.org'}
    assert entries[2].actor_user_id == user.id
    assert entries[2].request_id and entries[2].ip == '127.0.0.1'


def test_chore_changes_are_recorded_with_diff(client, login):
    admin = make_admin()
    login(admin)
    client.post('/chores/new', data={'title': 'Küche', 'assigned_to_id': 'None', 'due_date': ''})
    chore = db.session.scalar(select(Chore))
    client.post(f'/chores/edit/{chore.id}', data={'title': 'Bad', 'assigned_to_id': 'None', 'due_date': ''})
    entry = db.session.scalars(select(AuditLog).where(AuditLog.action == 'chore.update')).one()
    assert entry.diff == {'title': ['Küche', 'Bad']}
    assert entry.actor_user_id == admin.id


def test_responses_carry_a_request_id(client):
    assert len(client.get('/login').headers['X-Request-ID']) == 32


def test_audit_page_is_for_admins_only(client, login):
    make_user()
    login('max@example.org')
    assert client.get('/admin/audit').status_code in (302, 403)
    client.post('/logout')
    make_admin()
    login('admin@example.org')
    page = client.get('/admin/audit?action=auth.').get_data(as_text=True)
    assert 'auth.login' in page


def test_purge_ips_removes_old_addresses(app):
    old = AuditLog(action='x', ip='1.2.3.4', occurred_at=utcnow() - timedelta(days=400))
    new = AuditLog(action='y', ip='5.6.7.8')
    db.session.add_all([old, new])
    db.session.commit()
    result = app.test_cli_runner().invoke(purge_ips)
    assert result.exit_code == 0, result.output
    db.session.expire_all()
    assert db.session.get(AuditLog, old.id).ip is None
    assert db.session.get(AuditLog, new.id).ip == '5.6.7.8'
