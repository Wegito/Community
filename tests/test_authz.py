from datetime import date, timedelta

import pytest
from sqlalchemy import select

from community.core.authz.decorators import require_perm
from community.core.authz.models import FORMER_MEMBER, SYSADMIN, MemberRole, Permission, Role, RolePermission
from community.core.authz.registry import PERMISSIONS
from community.core.authz.service import (
    active_role_ids,
    end_member_role,
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

from .factories import give_permissions, make_member, make_user


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
            assert code in PERMISSIONS or code.split('.')[0] in planned, (role_key, code)


def test_seed_never_removes_grants(app):
    sysadmin = role_by_key(SYSADMIN)
    extra = db.session.scalar(select(Permission).where(Permission.code == 'tasks.manage'))
    db.session.add(RolePermission(role_id=sysadmin.id, permission_id=extra.id))
    db.session.commit()
    result = seed_grants()
    assert result['added'] == 0
    assert db.session.get(RolePermission, (sysadmin.id, extra.id)) is not None


def test_sysadmin_has_no_finance_write_rights():
    assert not [code for code in load_seed()[SYSADMIN] if code.startswith('finance.')]


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
    assert 'noch keine Rechte' in page


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
