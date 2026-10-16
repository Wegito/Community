import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from community.core.org.services import rank_by_key
from community.core.platform import notify as notify_module
from community.core.platform.audit import AuditLog
from community.core.platform.jobs import Job
from community.core.platform.notify import (
    EMAIL,
    IN_APP,
    KINDS,
    Notification,
    NotificationKind,
    NotificationPref,
    declare_kind,
    mark_all_read,
    notify,
    save_preferences,
    unread_count,
)
from community.extensions import db

from .factories import make_member, make_officer, make_user, set_rank


def mail_jobs():
    return db.session.scalars(select(Job).where(Job.kind == 'mail.send').order_by(Job.id)).all()


def notifications(member):
    return db.session.scalars(select(Notification).where(Notification.member_id == member.id)).all()


def test_declared_kinds():
    assert {'welcome', 'new_member', 'member_exit', 'password_reset', 'email_changed', 'invite'} <= set(KINDS)
    assert {'office_assigned', 'rank_changed'} <= set(KINDS)
    assert KINDS['password_reset'].mandatory and KINDS['password_reset'].channels == (EMAIL,)
    assert KINDS['email_changed'].mandatory and KINDS['email_changed'].channels == (EMAIL,)
    assert KINDS['invite'].channels == (EMAIL,)


def test_declare_kind_rejects_conflicts():
    with pytest.raises(ValueError, match='twice'):
        declare_kind('welcome', 'Anders')
    with pytest.raises(ValueError, match='channels'):
        declare_kind('test.sms', 'SMS', channels=('sms',))
    assert declare_kind('welcome', KINDS['welcome'].label) == 'welcome'  # same settings again is fine


def test_notify_unknown_kind(app):
    member = make_member()
    with pytest.raises(ValueError, match='Unknown notification kind'):
        notify(member, 'no.such.kind', 'x')


def test_notify_defaults_to_bell_and_email(app):
    app.config['BASE_URL'] = 'https://club.example'
    user = make_user(email='max@example.org')
    item = notify(user.member, 'office_assigned', 'Du bist Kassenwart.', link='/org/offices/kassenwart')
    db.session.commit()
    assert item.id and item.read_at is None and item.link == '/org/offices/kassenwart'
    (job,) = mail_jobs()
    assert job.payload['to'] == 'max@example.org'
    assert job.payload['subject'] == 'Du bist Kassenwart.'
    assert 'https://club.example/org/offices/kassenwart' in job.payload['body']
    assert 'https://club.example/settings/notifications' in job.payload['body']


def test_email_goes_to_login_then_contact_address(app):
    user = make_user(email='login@example.org')
    user.member.email = 'contact@example.org'
    no_login = make_member('Alter Herr', email='ah@example.org')
    nobody = make_member('Ohne Adresse')
    for member in (user.member, no_login, nobody):
        notify(member, 'rank_changed', 'Neuer Rang')
    db.session.commit()
    assert [job.payload['to'] for job in mail_jobs()] == ['login@example.org', 'ah@example.org']
    # Without any address the bell still works.
    assert len(notifications(nobody)) == 1


def test_preferences_switch_channels_off(app):
    user = make_user()
    member = user.member
    save_preferences(member, {('rank_changed', EMAIL), ('office_assigned', IN_APP)})
    db.session.commit()
    assert notify(member, 'office_assigned', 'Amt') is not None
    assert notify(member, 'rank_changed', 'Rang') is None  # bell off for this kind
    db.session.commit()
    assert [job.payload['subject'] for job in mail_jobs()] == ['Rang']  # email off for office_assigned


def test_default_email_off(app, monkeypatch):
    monkeypatch.setitem(KINDS, 'test.quiet', NotificationKind('test.quiet', 'Leise', default_email=False))
    member = make_user().member
    assert notify(member, 'test.quiet', 'Leise') is not None
    db.session.commit()
    assert mail_jobs() == []
    save_preferences(member, {('test.quiet', IN_APP), ('test.quiet', EMAIL)})
    notify(member, 'test.quiet', 'Jetzt mit Mail')
    db.session.commit()
    assert len(mail_jobs()) == 1


def test_mandatory_kinds_ignore_preferences(app):
    member = make_user(email='max@example.org').member
    # Even a stored "off" (e.g. written before the kind became mandatory) is ignored.
    db.session.add(NotificationPref(member_id=member.id, kind='password_reset', channel=EMAIL, enabled=False))
    db.session.commit()
    assert notify(member, 'password_reset', 'Passwort zurücksetzen') is None  # email only: no bell entry
    db.session.commit()
    (job,) = mail_jobs()
    assert job.payload['to'] == 'max@example.org'
    assert 'stellst du hier ein' not in job.payload['body']  # no settings link for mandatory mails


def test_save_preferences_skips_mandatory_kinds_and_audits_changes(app):
    member = make_user().member
    everything_on = {(kind, channel) for kind, d in KINDS.items() for channel in d.channels}
    assert save_preferences(member, everything_on) == {}  # same as the defaults: nothing changed
    db.session.commit()
    assert db.session.scalar(select(AuditLog).where(AuditLog.action == 'notify.preferences')) is None
    changed = save_preferences(member, everything_on - {('welcome', EMAIL)})
    db.session.commit()
    assert changed == {'welcome.email': False}
    entry = db.session.scalar(select(AuditLog).where(AuditLog.action == 'notify.preferences'))
    assert entry.diff == {'welcome.email': False}
    kinds = set(db.session.scalars(select(NotificationPref.kind).where(NotificationPref.member_id == member.id)))
    assert 'password_reset' not in kinds and 'email_changed' not in kinds


def test_unread_count_and_mark_all_read(app):
    member = make_user().member
    other = make_user(email='other@example.org', name='Other Person').member
    for title in ('a', 'b'):
        notify(member, 'rank_changed', title)
    notify(other, 'rank_changed', 'c')
    db.session.commit()
    assert unread_count(member) == 2
    assert mark_all_read(member) == 2
    db.session.commit()
    assert unread_count(member) == 0
    assert unread_count(other) == 1


# --- Pages ---------------------------------------------------------------------


def test_pages_need_login(client):
    for path in ('/notifications', '/settings/notifications'):
        response = client.get(path)
        assert response.status_code == 302 and '/login' in response.location
    assert '/login' in client.post('/notifications/read-all').location


def test_bell_and_list_newest_first(client, login):
    user = make_user()
    first = notify(user.member, 'rank_changed', 'Erste Nachricht')
    db.session.flush()
    second = notify(user.member, 'office_assigned', 'Zweite Nachricht', link='/org/offices')
    second.created_at = first.created_at + timedelta(days=365)
    db.session.commit()
    login(user)
    page = client.get('/notifications').get_data(as_text=True)
    assert page.index('Zweite Nachricht') < page.index('Erste Nachricht')
    assert 'data-unread="2"' in page
    assert 'Alle als gelesen markieren' in page

    client.post('/notifications/read-all')
    page = client.get('/notifications').get_data(as_text=True)
    assert 'data-unread' not in page
    assert unread_count(user.member) == 0


def test_bell_is_not_shown_to_anonymous_visitors(client):
    assert 'Benachrichtigungen' not in client.get('/login').get_data(as_text=True)


def test_open_marks_read_and_follows_the_link(client, login):
    user = make_user()
    with_link = notify(user.member, 'office_assigned', 'Amt', link='/org/offices')
    without_link = notify(user.member, 'rank_changed', 'Rang')
    db.session.commit()
    login(user)
    response = client.post(f'/notifications/{with_link.id}/read')
    assert response.status_code == 302 and response.location == '/org/offices'
    db.session.refresh(with_link)
    assert with_link.read_at is not None
    response = client.post(f'/notifications/{without_link.id}/read')
    assert response.location == '/notifications'


def test_open_never_redirects_off_site(client, login):
    user = make_user()
    item = notify(user.member, 'rank_changed', 'x', link='//evil.example/phish')
    db.session.commit()
    login(user)
    assert client.post(f'/notifications/{item.id}/read').location == '/notifications'


def test_cannot_open_someone_elses_notification(client, login):
    owner = make_user(email='owner@example.org', name='Owner Person')
    item = notify(owner.member, 'rank_changed', 'Privat')
    db.session.commit()
    login(make_user(email='other@example.org', name='Other Person'))
    assert client.post(f'/notifications/{item.id}/read').status_code == 404
    assert client.post('/notifications/999/read').status_code == 404
    db.session.refresh(item)
    assert item.read_at is None
    assert 'Privat' not in client.get('/notifications').get_data(as_text=True)


def test_settings_page(client, login):
    user = make_user()
    login(user)
    page = client.get('/settings/notifications').get_data(as_text=True)
    # Mandatory kinds are shown, checked and disabled.
    assert re.search(r'value="password_reset:email" [^>]*checked disabled', page)
    assert 'value="password_reset:in_app"' not in page  # email-only kind
    assert re.search(r'value="office_assigned:email" [^>]*checked>', page)

    # Everything off, plus a forged attempt to switch off a mandatory kind (it is simply absent).
    response = client.post('/settings/notifications', data={'on': ['office_assigned:in_app', 'bogus']})
    assert response.status_code == 302
    page = client.get('/settings/notifications').get_data(as_text=True)
    assert re.search(r'value="office_assigned:in_app" [^>]*checked>', page)
    assert not re.search(r'value="office_assigned:email" [^>]*checked', page)
    assert re.search(r'value="password_reset:email" [^>]*checked disabled', page)
    prefs = {
        (p.kind, p.channel): p.enabled
        for p in db.session.scalars(select(NotificationPref).where(NotificationPref.member_id == user.member_id))
    }
    assert prefs[('office_assigned', IN_APP)] is True
    assert prefs[('office_assigned', EMAIL)] is False
    assert ('password_reset', EMAIL) not in prefs


def test_settings_warns_without_email_address(client, login, monkeypatch):
    user = make_user()
    login(user)
    monkeypatch.setattr(notify_module, 'mail_address', lambda member: None)
    assert 'keine E-Mail-Adresse' in client.get('/settings/notifications').get_data(as_text=True)


# --- Wired into org --------------------------------------------------------------


def test_office_assignment_notifies_the_new_holder(client, login):
    senior = make_officer('senior')
    target = make_member('Kai Kasse', email='kai@example.org')
    set_rank(target, 'bursch')
    login(senior)
    client.post(
        '/org/offices/kassenwart/assign',
        data={'member_id': target.id, 'valid_from': '2026-10-01', 'semester_id': 'None', 'end_previous': 'y'},
    )
    (item,) = notifications(target)
    assert item.kind == 'office_assigned'
    assert item.title == 'Du bist ab 01.10.2026 Kassenwart.'
    assert item.link == '/org/offices/kassenwart'
    (job,) = mail_jobs()
    assert job.payload['to'] == 'kai@example.org'
    assert job.payload['subject'] == 'Neues Amt: Kassenwart'
    assert 'http://localhost/org/offices/kassenwart' in job.payload['body']
    assert 'Finanzen' in job.payload['body']  # the office's job description
    assert notifications(senior.member) == []


def test_refused_office_assignment_notifies_nobody(client, login):
    senior = make_officer('senior')
    login(senior)
    client.post(
        '/org/offices/kassenwart/assign',
        data={'member_id': senior.member_id, 'valid_from': '2026-10-01', 'semester_id': 'None', 'end_previous': 'y'},
    )
    assert db.session.scalars(select(Notification)).all() == []
    assert mail_jobs() == []


def test_rank_change_notifies_the_member(client, login):
    senior = make_officer('senior')
    target = make_member('Felix Fux', email='felix@example.org')
    set_rank(target, 'fux')
    login(senior)
    response = client.post(
        f'/org/members/{target.public_id}/rank',
        data={'rank_id': rank_by_key('bursch').id, 'valid_from': '2026-10-01'},
    )
    assert response.status_code == 302
    (item,) = notifications(target)
    assert (item.kind, item.title) == ('rank_changed', 'Du bist ab 01.10.2026 Bursch.')
    assert [job.payload['to'] for job in mail_jobs()] == ['felix@example.org']


def test_cli_rank_change_works_without_a_request(app):
    from community.core.org.cli import org_cli

    user = make_user(email='boot@example.org', perms=[])
    result = app.test_cli_runner().invoke(
        org_cli, ['set-rank', '--email', 'boot@example.org', '--rank', 'bursch', '--from', '2026-10-01']
    )
    assert result.exit_code == 0, result.output
    # Bootstrap through the CLI does not notify, so it needs neither a request nor BASE_URL.
    assert notifications(user.member) == []
