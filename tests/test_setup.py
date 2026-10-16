"""Install and update exactly as the README says (review findings 16, 17, 18, 24).

First install: `flask db upgrade`, `flask setup`, `flask create-admin`. Update of an M0 server:
`flask db upgrade`, `flask setup`, then the admin gives the migrated accounts their first rank.
"""

import re
from datetime import timedelta

from flask_migrate import upgrade
from sqlalchemy import select, text
from werkzeug.security import generate_password_hash

from community import create_app
from community.config import TestingConfig
from community.core.authz.models import SYSADMIN, Permission, RolePermission
from community.core.authz.service import role_by_key
from community.core.identity.models import MemberStatus, User
from community.core.org.models import RankHistory
from community.core.org.services import current_rank, rank_by_key
from community.core.platform.audit import AuditLog
from community.core.time import today_local
from community.extensions import db

from .conftest import FreshContextClient
from .factories import make_admin, make_member, make_user

PASSWORD = 'supersecret123'


def _app(path):
    class Config(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{path}'

    app = create_app(Config)
    app.test_client_class = FreshContextClient
    return app


def _logged_in(app, email):
    client = app.test_client()
    response = client.post('/login', data={'email': email, 'password': PASSWORD})
    assert response.status_code == 302 and response.headers['Location'] == '/', email
    return client


def test_first_install_as_in_the_readme(tmp_path):
    app = _app(tmp_path / 'new.db')
    runner = app.test_cli_runner()
    with app.app_context():
        try:
            upgrade()  # flask db upgrade
            result = runner.invoke(args=['setup'])
            assert result.exit_code == 0, result.output
            assert 'No admin yet' in result.output
            result = runner.invoke(
                args=['create-admin', '--email', 'admin@example.org', '--name', 'Anna Admin', '--password', PASSWORD]
            )
            assert result.exit_code == 0 and 'Warning' not in result.output, result.output

            admin = _logged_in(app, 'admin@example.org')
            for path in ('/members', '/members/new', '/members/import', '/org/offices', '/org/ranks/first'):
                assert admin.get(path).status_code == 200, path
            # The admin invites a new member by link; they join and can use the app.
            response = admin.post(
                '/members/new',
                data={
                    'first_name': 'Neu',
                    'last_name': 'Ling',
                    'joined_on': today_local().isoformat(),
                    'rank_id': rank_by_key('spefux').id,
                    'delivery': 'link',
                },
            )
            assert response.status_code == 302
            token = re.search(r'/join/([A-Za-z0-9_-]+)', admin.get(response.headers['Location']).get_data(as_text=True))
            newcomer = app.test_client()
            response = newcomer.post(
                f'/join/{token.group(1)}',
                data={'login_email': 'neu@example.org', 'password': PASSWORD, 'confirm': PASSWORD},
            )
            assert response.status_code == 302
            assert newcomer.get('/announcements').status_code == 200

            # Running it again (every update) adds nothing.
            again = runner.invoke(args=['setup'])
            assert again.exit_code == 0, again.output
            assert 'Permissions: 0 added' in again.output and 'Default grants: none added' in again.output
            assert 'No admin' not in again.output
        finally:
            db.session.remove()
            db.engine.dispose()


def test_update_from_m0_as_in_the_readme(tmp_path):
    app = _app(tmp_path / 'm0.db')
    runner = app.test_cli_runner()
    with app.app_context():
        try:
            upgrade(revision='0003')  # what an M0 server has
            with db.engine.begin() as conn:
                for user_id, email, name, role in [
                    (1, 'old@example.org', 'Olga Admin', 'admin'),
                    (2, 'res@example.org', 'Rudi Resident', 'resident'),
                ]:
                    conn.execute(
                        text(
                            'INSERT INTO "user" (id, email, name, password_hash, role) '
                            'VALUES (:id, :email, :name, :password_hash, :role)'
                        ),
                        {
                            'id': user_id,
                            'email': email,
                            'name': name,
                            'password_hash': generate_password_hash(PASSWORD),
                            'role': role,
                        },
                    )
                conn.execute(text("INSERT INTO announcement (title, body, author_id) VALUES ('Alt', 'Text', 2)"))
            upgrade()
            result = runner.invoke(args=['setup'])
            assert result.exit_code == 0, result.output
            assert 'Rudi Resident <res@example.org>' in result.output
            assert 'No admin' not in result.output

            admin = _logged_in(app, 'old@example.org')
            resident = _logged_in(app, 'res@example.org')
            assert admin.get('/members').status_code == 200
            assert resident.get('/announcements').status_code == 403  # no rank, no rights yet
            assert 'Rudi Resident' in admin.get('/org/ranks/first').get_data(as_text=True)
            member_id = db.session.scalar(select(User.member_id).where(User.login_email == 'res@example.org'))
            response = admin.post(
                '/org/ranks/first',
                data={'valid_from': today_local().isoformat(), f'rank-{member_id}': rank_by_key('bursch').id},
            )
            assert response.status_code == 302
            for path in ('/announcements', '/chores', '/bookings'):
                assert resident.get(path).status_code == 200, path
            assert f'name="rank-{member_id}"' not in admin.get('/org/ranks/first').get_data(as_text=True)
        finally:
            db.session.remove()
            db.engine.dispose()


def test_setup_needs_the_upgraded_database(tmp_path):
    app = _app(tmp_path / 'empty.db')
    with app.app_context():
        try:
            upgrade(revision='0005')
            result = app.test_cli_runner().invoke(args=['setup'])
            assert result.exit_code != 0 and 'flask db upgrade' in result.output
        finally:
            db.session.remove()
            db.engine.dispose()


def test_setup_seeds_only_new_codes_once_grants_exist(app):
    """Grants removed on purpose stay removed; a code new in this version gets its default grants."""
    sysadmin = role_by_key(SYSADMIN)

    def grant(code):
        permission = db.session.scalar(select(Permission).where(Permission.code == code))
        return permission and db.session.get(RolePermission, (sysadmin.id, permission.id))

    db.session.delete(grant('bookings.manage_any'))  # an admin's edit
    db.session.delete(db.session.scalar(select(Permission).where(Permission.code == 'audit.view')))  # a new code
    db.session.commit()
    result = app.test_cli_runner().invoke(args=['setup'])
    assert result.exit_code == 0, result.output
    assert 'Permissions: 1 added' in result.output
    assert grant('audit.view') is not None
    assert grant('bookings.manage_any') is None
    # --seed adds every missing default grant.
    result = app.test_cli_runner().invoke(args=['setup', '--seed'])
    assert result.exit_code == 0, result.output
    assert grant('bookings.manage_any') is not None


def test_first_ranks_page(client, login):
    admin = make_admin()
    rankless = make_member('Rudi Resident', email='res@example.org')
    ranked = make_user(email='b@example.org', name='Bea Bursch')
    former = make_member('Ex Mitglied', status=MemberStatus.ENDED)
    login(admin)
    page = client.get('/org/ranks/first').get_data(as_text=True)
    assert 'Rudi Resident' in page and 'Bea Bursch' not in page and 'Ex Mitglied' not in page
    assert f'name="rank-{admin.member_id}"' not in page  # nobody sets their own rank
    bursch, fux = rank_by_key('bursch'), rank_by_key('fux')

    # Not in the future, and nothing at all for members who have (had) a rank.
    tomorrow = (today_local() + timedelta(days=1)).isoformat()
    page = client.post('/org/ranks/first', data={'valid_from': tomorrow, f'rank-{rankless.id}': bursch.id})
    assert 'nicht in der Zukunft' in page.get_data(as_text=True)
    assert db.session.scalar(select(RankHistory).where(RankHistory.member_id == rankless.id)) is None
    client.post(
        '/org/ranks/first',
        data={
            'valid_from': today_local().isoformat(),
            f'rank-{rankless.id}': bursch.id,
            f'rank-{ranked.member_id}': fux.id,
            f'rank-{former.id}': fux.id,
            f'rank-{admin.member_id}': fux.id,
        },
    )
    db.session.expire_all()
    assert current_rank(rankless) == bursch
    assert current_rank(ranked.member) == bursch
    assert db.session.scalar(select(RankHistory).where(RankHistory.member_id == former.id)) is None
    assert current_rank(admin.member) is None
    entry = db.session.scalar(select(AuditLog).where(AuditLog.action == 'org.rank.change').order_by(AuditLog.id.desc()))
    assert (entry.entity_id, entry.actor_user_id) == (str(rankless.id), admin.id)
    assert entry.diff['from'] is None and entry.diff['to'] == 'bursch'
    assert f'name="rank-{rankless.id}"' not in client.get('/org/ranks/first').get_data(as_text=True)


def test_first_ranks_page_needs_members_import(client, login):
    user = make_user(perms=['members.directory.view', 'members.rank.change'])
    login(user)
    assert client.get('/org/ranks/first').status_code == 403


def test_set_rank_cli_finds_members_without_login(app):
    member = make_member('Imp Ortiert', email='imp@example.org')
    result = app.test_cli_runner().invoke(args=['org', 'set-rank', '--email', 'imp@example.org', '--rank', 'fux'])
    assert result.exit_code == 0, result.output
    assert current_rank(member) == rank_by_key('fux')
