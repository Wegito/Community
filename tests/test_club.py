"""The club's name is data: another club runs its own copy without touching the code."""

import pytest
from sqlalchemy import select

from community.core.club import Club, club_name, club_short_name, update_club
from community.core.platform.audit import AuditLog
from community.core.platform.mail import render_mail
from community.extensions import db

from .factories import make_admin, make_user


def test_name_comes_from_the_config_until_saved(app):
    assert db.session.scalar(select(Club)) is None
    assert club_name() == app.config['CLUB_NAME']
    assert club_short_name() == app.config['CLUB_NAME']


def test_saved_name_and_short_name(app):
    admin = make_admin()
    diff = update_club('  VDSt Musterstadt  ', 'VDSt MS', actor=admin)
    db.session.commit()
    assert club_name() == 'VDSt Musterstadt'
    assert club_short_name() == 'VDSt MS'
    assert diff['name'][1] == 'VDSt Musterstadt'
    (entry,) = db.session.scalars(select(AuditLog).where(AuditLog.action == 'club.update')).all()
    assert entry.actor_user_id == admin.id

    update_club('VDSt Musterstadt', '', actor=admin)
    assert club_short_name() == 'VDSt Musterstadt'
    with pytest.raises(ValueError):
        update_club('   ', actor=admin)


def test_first_save_is_audited_even_without_a_change(app):
    admin = make_admin()
    assert update_club(app.config['CLUB_NAME'], actor=admin) == {}
    db.session.commit()
    assert db.session.scalar(select(Club)).name == app.config['CLUB_NAME']
    assert db.session.scalar(select(AuditLog).where(AuditLog.action == 'club.update')) is not None


def test_admin_renames_the_club_and_every_page_and_mail_follows(app, client, login):
    login(make_admin())
    page = client.get('/admin/verein')
    assert page.status_code == 200
    assert 'CLUB_NAME' in page.get_data(as_text=True)

    response = client.post('/admin/verein', data={'name': 'Corps Beispiel', 'short_name': 'Beispiel'})
    assert response.status_code == 302
    html = client.get('/').get_data(as_text=True)
    assert 'Corps Beispiel</title>' in html
    assert '>Beispiel</a>' in html
    subject, body = render_mail('password_reset', name='Max', reset_url='http://x', minutes=60)
    assert 'Corps Beispiel' in body


def test_empty_name_is_refused(client, login):
    login(make_admin())
    page = client.post('/admin/verein', data={'name': '  ', 'short_name': ''})
    assert page.status_code == 200
    assert db.session.scalar(select(Club)) is None


def test_only_the_technical_admin_renames_the_club(client, login):
    login(make_user(email='member@example.org', name='Ein Mitglied'))
    assert client.post('/admin/verein', data={'name': 'Gekapert'}).status_code == 403
    assert db.session.scalar(select(Club)) is None
