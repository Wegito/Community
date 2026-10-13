from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from community.core.authz.decorators import require_perm
from community.core.authz.models import FORMER_MEMBER, SYSADMIN, MemberRole, Permission, Role, RolePermission
from community.core.authz.registry import PERMISSIONS
from community.core.authz.service import (
    active_role_ids,
    end_member_role,
    expand_codes,
    grant_member_role,
    holders_of,
    load_seed,
    permissions_for,
    role_by_key,
    seed_grants,
    sync_permissions,
)
from community.core.identity.models import MemberStatus
from community.core.time import today_local
from community.extensions import db

from .factories import give_permissions, make_admin, make_member, make_user


def test_every_declared_code_is_synced(app):
    codes = set(db.session.scalars(select(Permission.code)))
    assert codes == set(PERMISSIONS)


def test_sync_reports_stale_codes(app):
    db.session.add(Permission(code='gone.away', module='gone', description='x'))
    db.session.commit()
    added, updated, stale = sync_permissions()
    assert (added, updated, stale) == (0, 0, ['gone.away'])


def test_require_perm_rejects_undeclared_codes():
    with pytest.raises(ValueError, match='not declared'):
        require_perm('does.not.exist')


def test_seed_codes_are_declared_or_planned():
    """Every code in the seed is either declared now or belongs to a module that is still planned."""
    planned = {'events', 'finance', 'drinks', 'laundry', 'fux', 'documents'}
    for role_key, codes in load_seed().items():
        for code in codes:
            if code.startswith('!') or any(char in code for char in '*?['):
                module = code.lstrip('!').split('.')[0]
                assert module == '*' or module in planned or any(c.startswith(module + '.') for c in PERMISSIONS)
                continue
            assert code in PERMISSIONS or code.split('.')[0] in planned, (role_key, code)


def test_every_declared_code_is_in_the_roadmap_matrix(app):
    """§8.1: a new permission code also goes into the default matrix (ROADMAP.md §6.5)."""
    roadmap = (Path(__file__).resolve().parent.parent / 'docs' / 'ROADMAP.md').read_text(encoding='utf-8')
    matrix = roadmap.split('### 6.5 Default permission matrix')[1].split('### 6.6')[0]
    rows = [line.split('  ')[0] for line in matrix.splitlines() if '│' in line]  # "a.b, a.c (note)"
    listed = {code.strip().split(' ')[0] for row in rows for code in row.split(',')}
    assert sorted(set(PERMISSIONS) - listed) == []


def test_seed_never_removes_grants(app):
    spefux = role_by_key('rank.spefux')
    extra = db.session.scalar(select(Permission).where(Permission.code == 'tasks.manage'))
    db.session.add(RolePermission(role_id=spefux.id, permission_id=extra.id))
    db.session.commit()
    result = seed_grants()
    assert result['added'] == 0
    assert db.session.get(RolePermission, (spefux.id, extra.id)) is not None


def test_sysadmin_has_everything_except_the_kasse():
    """Owner, 2026-10-13: the technical admin has full control, but only Kassenwart and Kassenprüfer see the Kasse."""
    codes = set(expand_codes(load_seed()[SYSADMIN], PERMISSIONS))
    assert not {code for code in codes if code.startswith('finance.')}
    assert set(PERMISSIONS) - codes == {code for code in PERMISSIONS if code.startswith('finance.')}


def test_seed_patterns():
    codes = ['a.x', 'a.y', 'b.x', 'finance.post']
    assert expand_codes(['*', '!finance.*'], codes) == ['a.x', 'a.y', 'b.x']
    assert expand_codes(['a.*', 'b.x', 'a.x'], codes) == ['a.x', 'a.y', 'b.x']
    assert expand_codes(['!a.y', 'a.*'], codes) == ['a.x']
    assert expand_codes(['planned.code'], codes) == ['planned.code']


def test_direct_role_respects_dates(app):
    member = make_member()
    role = role_by_key(SYSADMIN)
    db.session.add(MemberRole(member_id=member.id, role_id=role.id, valid_from=today_local() + timedelta(days=1)))
    db.session.commit()
    assert role.id not in active_role_ids(member)
    assert role.id in active_role_ids(member, today_local() + timedelta(days=1))


def test_end_member_role(app):
    member = make_member()
    grant_member_role(member, SYSADMIN, valid_from=date(2020, 1, 1))
    db.session.commit()
    end_member_role(member, SYSADMIN, on=today_local() - timedelta(days=1))
    db.session.commit()
    assert 'system.admin' not in permissions_for(member)


def test_former_member_only_keeps_former_member_role(app):
    user = make_user()
    user.member.status = MemberStatus.ENDED
    db.session.commit()
    assert active_role_ids(user.member) == {role_by_key(FORMER_MEMBER).id}
    assert 'announcements.view' not in permissions_for(user.member)


def test_holders_of(app):
    holder = make_user(email='a@example.org', name='A Holder', perms=['audit.view'])
    make_user(email='b@example.org', name='B Other', perms=[])
    assert [m.id for m in holders_of('audit.view')] == [holder.member_id]


def test_permissions_are_cached_per_request_and_fresh_next_request(client, login):
    user = make_user(perms=[])
    login(user)
    assert client.get('/chores').status_code == 403
    give_permissions(user.member, 'tasks.view_own')
    assert client.get('/chores').status_code == 200


def test_navigation_shows_only_allowed_links(client, login):
    make_user(perms=['tasks.view_own'])
    login('max@example.org')
    page = client.get('/').get_data(as_text=True)
    assert 'Putzplan' in page
    assert 'Buchungen' not in page
    assert 'Protokoll' not in page
    assert 'noch keine Rechte' not in page  # one right is not "no rights"


NO_RIGHTS = 'Dein Konto hat noch keine Rechte'


def test_start_page_for_a_member(client, login):
    login(make_user())
    page = client.get('/').get_data(as_text=True)
    assert 'Neueste Ankündigungen' in page and NO_RIGHTS not in page


def test_start_page_without_any_rights(client, login):
    login(make_user(perms=[]))
    page = client.get('/').get_data(as_text=True)
    assert NO_RIGHTS in page and 'Neueste Ankündigungen' not in page


def test_start_page_for_the_technical_admin(client, login):
    # custom.sysadmin has every right except the Kasse (owner, 2026-10-13).
    login(make_admin())
    page = client.get('/').get_data(as_text=True)
    assert NO_RIGHTS not in page and 'Neueste Ankündigungen' in page and 'Verwaltung' in page


def test_start_page_for_a_former_member(client, login):
    from community.core.identity.models import ExitReason
    from community.modules.members.services import exit_member

    user = make_user()
    exit_member(user.member, today_local(), ExitReason.AUSTRITT, recorded_by=make_admin())
    db.session.commit()
    login(user)
    page = client.get('/').get_data(as_text=True)
    assert NO_RIGHTS not in page and 'Neueste Ankündigungen' not in page
    assert 'ehemaliges Mitglied' in page and 'href="/profile"' in page


def test_member_without_view_all_sees_only_own_chores(client, login):
    from community.modules.tasks.models import Chore

    me = make_user(perms=['tasks.view_own'])
    other = make_user(email='o@example.org', name='Other Person')
    db.session.add_all([Chore(title='Meine', assigned_to_id=me.id), Chore(title='Fremde', assigned_to_id=other.id)])
    db.session.commit()
    login(me)
    page = client.get('/chores').get_data(as_text=True)
    assert 'Meine' in page
    assert 'Fremde' not in page


def test_authz_cli_grant_show_revoke(app):
    from community.core.authz.cli import authz_cli

    make_user(perms=[])
    runner = app.test_cli_runner()
    assert runner.invoke(authz_cli, ['grant', '--email', 'max@example.org', '--role', SYSADMIN]).exit_code == 0
    shown = runner.invoke(authz_cli, ['show', '--email', 'max@example.org'])
    assert SYSADMIN in shown.output and 'system.admin' in shown.output
    assert runner.invoke(authz_cli, ['grant', '--email', 'max@example.org', '--role', 'nope']).exit_code != 0
    assert runner.invoke(authz_cli, ['revoke', '--email', 'max@example.org', '--role', SYSADMIN]).exit_code == 0


def test_authz_cli_revoke_takes_effect_at_once(app):
    """Review finding 12: valid_to is inclusive, so revoking must not end the role "today"."""
    from community.core.authz.cli import authz_cli

    member = make_user(perms=[]).member
    grant_member_role(member, SYSADMIN, valid_from=date(2020, 1, 1))
    grant_member_role(member, SYSADMIN, valid_from=today_local())
    grant_member_role(member, SYSADMIN, valid_from=today_local() + timedelta(days=3))
    db.session.commit()
    result = app.test_cli_runner().invoke(authz_cli, ['revoke', '--email', 'max@example.org', '--role', SYSADMIN])
    assert result.exit_code == 0 and '3 assignment(s)' in result.output
    db.session.expire_all()
    assert 'system.admin' not in permissions_for(member)
    assert 'system.admin' not in permissions_for(member, today_local() + timedelta(days=5))
    # The old grant ends yesterday; those that had not begun are gone.
    rows = db.session.scalars(select(MemberRole).where(MemberRole.member_id == member.id)).all()
    assert [(r.valid_from, r.valid_to) for r in rows] == [(date(2020, 1, 1), today_local() - timedelta(days=1))]
    shown = app.test_cli_runner().invoke(authz_cli, ['show', '--email', 'max@example.org'])
    assert SYSADMIN not in shown.output


def test_role_table_has_unique_keys(app):
    db.session.add(Role(key=SYSADMIN, label='dup', kind='custom'))
    with pytest.raises(Exception):  # noqa: B017
        db.session.commit()
    db.session.rollback()


def test_seed_writes_one_audit_entry_per_role(app):
    """Grants added by the seed are audited, sensitive codes named separately (finding 21, §6.3 rule 5)."""
    from community.core.authz.cli import authz_cli
    from community.core.platform.audit import AuditLog

    def drop(role_key, code):
        permission = db.session.scalar(select(Permission).where(Permission.code == code))
        db.session.delete(db.session.get(RolePermission, (role_by_key(role_key).id, permission.id)))

    def seed_entries():
        query = select(AuditLog).where(AuditLog.action == 'authz.role_permission.grant', AuditLog.id > before)
        return db.session.scalars(query.order_by(AuditLog.id)).all()

    drop(SYSADMIN, 'system.admin')
    drop(SYSADMIN, 'members.invite')
    drop('rank.fux', 'announcements.view')
    db.session.commit()
    before = db.session.scalar(select(func.max(AuditLog.id)))
    result = app.test_cli_runner().invoke(authz_cli, ['seed'])
    assert result.exit_code == 0, result.output
    assert 'Grants added: 3.' in result.output
    entries = seed_entries()
    assert [(e.entity_type, e.entity_id) for e in entries] == [
        ('role', str(role_by_key('rank.fux').id)),
        ('role', str(role_by_key(SYSADMIN).id)),
    ]
    assert entries[0].diff == {'role': 'rank.fux', 'codes': ['announcements.view'], 'source': 'authz.yaml'}
    assert entries[1].diff == {
        'role': SYSADMIN,
        'codes': ['members.invite', 'system.admin'],
        'sensitive': ['system.admin'],
        'source': 'authz.yaml',
    }
    # Nothing added, nothing recorded.
    app.test_cli_runner().invoke(authz_cli, ['seed'])
    assert len(seed_entries()) == 2
