"""M1-12: locking logins, logging out everywhere, Austritt and Wiederaufnahme."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from community.core.authz.models import SYSADMIN, MemberRole
from community.core.authz.service import active_roles, grant_member_role, holders_of
from community.core.identity.models import ExitReason, MemberStatus
from community.core.org.models import GroupMembership, OfficeAssignment, RankHistory
from community.core.org.services import (
    OrgError,
    add_to_group,
    assign_office,
    create_group,
    current_rank,
    office_by_key,
    office_holders,
)
from community.core.platform.audit import AuditLog
from community.core.platform.jobs import Job, run_due_jobs
from community.core.platform.notify import Notification
from community.core.time import today_local
from community.extensions import db
from community.modules.members.services import (
    OFFICE_END_REASON,
    MemberError,
    disable_login,
    enable_login,
    exit_member,
    logout_everywhere,
    reactivate_member,
)

from .factories import DEFAULT_PASSWORD, make_admin, make_member, make_officer, make_user

SINCE = date(2020, 1, 1)


def audit_entries(action):
    return db.session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)).all()


def notifications(member, kind='member_exit'):
    return db.session.scalars(
        select(Notification).where(Notification.member_id == member.id, Notification.kind == kind)
    ).all()


def sign_in(client, user, remember=False):
    data = {'email': user.login_email, 'password': DEFAULT_PASSWORD}
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


def reload(obj):
    db.session.expire_all()
    return db.session.get(type(obj), obj.id)


# --- Login: lock, unlock, log out everywhere (services) --------------------------


def test_disable_login_ends_all_sessions_and_is_audited(app):
    admin = make_admin()
    user = make_user()
    version = user.session_version
    disable_login(user.member, actor=admin)
    db.session.commit()
    assert not user.is_enabled and user.session_version == version + 1
    (entry,) = audit_entries('members.login.disable')
    assert entry.actor_user_id == admin.id and entry.entity_type == 'user' and entry.entity_id == str(user.id)

    enable_login(user.member, actor=admin)
    db.session.commit()
    assert user.is_enabled and user.session_version == version + 1
    assert [e.actor_user_id for e in audit_entries('members.login.enable')] == [admin.id]


def test_login_lock_rules(app):
    admin = make_admin()
    user = make_user()
    with pytest.raises(MemberError, match='eigenen Login'):
        disable_login(admin.member, actor=admin)
    with pytest.raises(MemberError, match='nicht gesperrt'):
        enable_login(user.member, actor=admin)
    disable_login(user.member, actor=admin)
    with pytest.raises(MemberError, match='bereits gesperrt'):
        disable_login(user.member, actor=admin)
    no_login = make_member('Otto Ohne')
    for action in (disable_login, enable_login):
        with pytest.raises(MemberError, match='keinen Login'):
            action(no_login, actor=admin)


def test_logout_everywhere_raises_the_session_version(app):
    user = make_user()
    version = user.session_version
    logout_everywhere(user, actor=user)
    db.session.commit()
    assert user.session_version == version + 1 and user.is_enabled
    (entry,) = audit_entries('members.logout_everywhere')
    assert entry.actor_user_id == user.id and entry.entity_id == str(user.id)


def test_disabled_account_has_no_session_even_with_the_current_version(app):
    from community.core.identity import load_user

    user = make_user()
    assert load_user(user.get_id()) is not None
    user.is_enabled = False
    db.session.commit()
    assert load_user(user.get_id()) is None


# --- Login: pages ----------------------------------------------------------------


def test_disabling_a_login_logs_the_member_out(app, client, login):
    target = make_user(email='target@example.org', name='Tim Target')
    target_client = sign_in(app.test_client(), target)
    assert is_logged_in(target_client)

    senior = make_officer('senior')
    login(senior)
    response = client.post(f'/m/{target.member.public_id}/login/disable', follow_redirects=True)
    assert 'Login von Tim Target gesperrt' in response.get_data(as_text=True)
    assert not reload(target).is_enabled

    assert not is_logged_in(target_client)
    response = app.test_client().post('/login', data={'email': target.login_email, 'password': DEFAULT_PASSWORD})
    assert 'Login fehlgeschlagen' in response.get_data(as_text=True)

    response = client.post(f'/m/{target.member.public_id}/login/enable', follow_redirects=True)
    assert 'Login von Tim Target entsperrt' in response.get_data(as_text=True)
    assert is_logged_in(sign_in(app.test_client(), target))


def test_own_login_cannot_be_disabled(client, login):
    senior = make_officer('senior')
    login(senior)
    response = client.post(f'/m/{senior.member.public_id}/login/disable', follow_redirects=True)
    assert 'Den eigenen Login kann man nicht sperren' in response.get_data(as_text=True)
    assert reload(senior).is_enabled
    assert is_logged_in(client)
    assert audit_entries('members.login.disable') == []


def test_member_without_login_has_no_login_buttons(client, login):
    target = make_member('Otto Ohne')
    login(make_officer('senior'))
    page = client.get(f'/m/{target.public_id}').get_data(as_text=True)
    assert 'Kein Login' in page
    assert 'Login sperren' not in page and 'Überall abmelden' not in page
    for action in ('login/disable', 'login/enable', 'logout-everywhere'):
        assert client.post(f'/m/{target.public_id}/{action}').status_code == 404


def test_login_routes_need_edit_any(client, login):
    target = make_user(email='target@example.org', name='Tim Target')
    login(make_user(email='bursch@example.org', name='Bernd Bursch'))
    for action in ('login/disable', 'login/enable', 'logout-everywhere'):
        assert client.post(f'/m/{target.member.public_id}/{action}').status_code == 403
    assert reload(target).is_enabled and reload(target).session_version == target.session_version


@pytest.mark.parametrize(
    'url', ['/profile/logout-everywhere', '/m/x/logout-everywhere', '/m/x/login/disable', '/m/x/reactivate']
)
def test_lifecycle_posts_need_a_login(client, url):
    response = client.post(url)
    assert response.status_code == 302 and '/login' in response.location


def test_logout_everywhere_own(app, client, login):
    user = make_user()
    other_device = sign_in(app.test_client(), user)
    remembered = sign_in(app.test_client(), user, remember=True)
    # Without its session cookie the third device still gets in through "Angemeldet bleiben".
    remembered.delete_cookie('session')
    assert remembered.get_cookie('session') is None and remembered.get_cookie('remember_token') is not None
    assert is_logged_in(remembered)
    remembered.delete_cookie('session')

    login(user)
    response = client.post('/profile/logout-everywhere')
    assert response.status_code == 302 and response.location.endswith('/login')
    assert not is_logged_in(client)
    assert not is_logged_in(other_device)
    assert not is_logged_in(remembered)
    (entry,) = audit_entries('members.logout_everywhere')
    assert entry.actor_user_id == user.id
    # A new login works as usual.
    assert is_logged_in(sign_in(app.test_client(), user))


def test_profile_has_a_logout_everywhere_button(client, login):
    login(make_user(perms=[]))
    page = client.get('/profile').get_data(as_text=True)
    assert 'action="/profile/logout-everywhere"' in page and 'Überall abmelden' in page


def test_logout_everywhere_by_an_admin(app, client, login):
    target = make_user(email='target@example.org', name='Tim Target')
    target_client = sign_in(app.test_client(), target)
    admin = make_admin()
    login(admin)
    response = client.post(f'/m/{target.member.public_id}/logout-everywhere', follow_redirects=True)
    assert 'Tim Target ist auf allen Geräten abgemeldet' in response.get_data(as_text=True)
    assert not is_logged_in(target_client)
    assert is_logged_in(client)
    assert reload(target).is_enabled
    (entry,) = audit_entries('members.logout_everywhere')
    assert entry.actor_user_id == admin.id and entry.entity_id == str(target.id)


def test_logout_everywhere_on_the_own_member_page_logs_out(client, login):
    admin = make_admin()
    login(admin)
    response = client.post(f'/m/{admin.member.public_id}/logout-everywhere')
    assert response.status_code == 302 and response.location.endswith('/login')
    assert not is_logged_in(client)


# --- Austritt (service) ----------------------------------------------------------


@pytest.fixture
def club(app):
    """A Kassenwart, a Senior and Max, a Bursch with an Amt, a group, a direct role and a login."""
    kassenwart = make_officer('kassenwart', name='Kai Kasse')
    senior = make_officer('senior', name='Sven Senior')
    max_ = make_officer('schriftwart', email='max@example.org', name='Max Muster')
    member = max_.member
    group = create_group('Küche A')
    add_to_group(group, member, SINCE)
    grant_member_role(member, SYSADMIN, valid_from=SINCE)
    db.session.commit()
    return {'kassenwart': kassenwart, 'senior': senior, 'max': max_, 'member': member, 'group': group}


def test_exit_ends_everything_and_notifies_the_kassenwart(app, club):
    member, senior = club['member'], club['senior']
    ended_on = today_local() - timedelta(days=30)
    # Already over before the exit: stays as it is. Starts after the exit: ends on its first day.
    old_group = create_group('Alte Gruppe')
    old = add_to_group(old_group, member, SINCE)
    old.valid_to = date(2021, 1, 1)
    later_group = create_group('Neue Gruppe')
    add_to_group(later_group, member, ended_on + timedelta(days=10))
    db.session.commit()

    notified = exit_member(member, ended_on, ExitReason.AUSTRITT, recorded_by=senior, note=' Umzug nach Bremen ')
    db.session.commit()

    assert [m.id for m in notified] == [club['kassenwart'].member_id]
    member = reload(member)
    assert (member.status, member.ended_on, member.ended_reason) == (MemberStatus.ENDED, ended_on, 'austritt')
    assert member.user.is_enabled  # the login stays

    (office,) = db.session.scalars(select(OfficeAssignment).where(OfficeAssignment.member_id == member.id)).all()
    assert office.valid_to == ended_on and office.end_reason == OFFICE_END_REASON
    groups = {
        m.group.name: m.valid_to
        for m in db.session.scalars(select(GroupMembership).where(GroupMembership.member_id == member.id))
    }
    assert groups == {
        'Küche A': ended_on,
        'Alte Gruppe': date(2021, 1, 1),
        'Neue Gruppe': ended_on + timedelta(days=10),
    }
    roles = db.session.scalars(select(MemberRole).where(MemberRole.member_id == member.id)).all()
    assert [r.valid_to for r in roles if r.role.key == SYSADMIN] == [ended_on]
    ranks = db.session.scalars(select(RankHistory).where(RankHistory.member_id == member.id)).all()
    assert [r.valid_to for r in ranks] == [ended_on]

    # Only the former-member role is left, and nobody counts them as a holder of anything.
    assert [r.key for r in active_roles(member)] == ['system.former_member']
    assert member not in holders_of('members.directory.view')
    assert office_holders(office_by_key('schriftwart')) == []

    (entry,) = audit_entries('members.exit')
    assert entry.actor_user_id == senior.id
    assert entry.diff == {
        'ended_on': ended_on.isoformat(),
        'reason': 'austritt',
        'note': 'Umzug nach Bremen',
        'ended': {
            'rank': ['bursch'],
            'offices': ['schriftwart'],
            'groups': ['Küche A', 'Neue Gruppe'],
            'roles': [SYSADMIN],
            'invitations': [],
        },
        'notified_member_ids': [club['kassenwart'].member_id],
    }
    assert [e.diff['office'] for e in audit_entries('org.office.end')] == ['schriftwart']

    (note,) = notifications(club['kassenwart'].member)
    assert 'Max Muster' in note.title and member.payment_ref in note.title
    assert f'{ended_on:%d.%m.%Y}' in note.title and 'Austritt' in note.title
    assert notifications(senior.member) == []
    (job,) = db.session.scalars(select(Job).where(Job.kind == 'mail.send')).all()
    assert job.payload['to'] == club['kassenwart'].login_email
    run_due_jobs()
    (mail,) = app.extensions['mail_outbox']
    assert mail['to'] == 'kassenwart@example.org' and 'Max Muster' in mail['body']


def test_exit_frees_the_office_for_a_successor(app, club):
    kassenwart = club['kassenwart'].member
    exit_member(kassenwart, today_local(), ExitReason.SONSTIGES, recorded_by=club['senior'])
    db.session.commit()
    successor = make_user(email='new@example.org', name='Nina Nachfolger').member
    # max_holders is 1; without the exit this would need end_previous.
    assignment = assign_office(
        office_by_key('kassenwart'), successor, today_local() + timedelta(days=1), end_previous=False
    )
    db.session.commit()
    assert assignment.id is not None


def test_exit_of_the_kassenwart_does_not_notify_themselves(app, club):
    kassenwart = club['kassenwart'].member
    notified = exit_member(kassenwart, today_local(), ExitReason.VERSTORBEN, recorded_by=club['senior'])
    db.session.commit()
    assert notified == [] and notifications(kassenwart) == []
    assert audit_entries('members.exit')[0].diff['notified_member_ids'] == []


@pytest.mark.parametrize(
    ('change', 'message'),
    [
        ({'ended_on': date.max}, 'Zukunft'),
        ({'ended_on': date(2019, 12, 31), 'joined_on': date(2020, 1, 1)}, 'vor dem Eintritt'),
        ({'status': MemberStatus.ENDED}, 'bereits ausgeschieden'),
        ({'own': True}, 'eigenen Austritt'),
    ],
)
def test_exit_rules(app, club, change, message):
    member, senior = club['member'], club['senior']
    if change.get('joined_on'):
        member.joined_on = change['joined_on']
    if change.get('status'):
        member.status = change['status']
    recorded_by = club['max'] if change.get('own') else senior
    with pytest.raises(MemberError, match=message):
        exit_member(member, change.get('ended_on', today_local()), ExitReason.AUSTRITT, recorded_by=recorded_by)
    with pytest.raises(ValueError, match='Unknown exit reason'):
        exit_member(club['kassenwart'].member, today_local(), 'weggelaufen', recorded_by=senior)


def test_reactivate_does_not_restore_rank_or_offices(app, club):
    member, senior = club['member'], club['senior']
    exit_member(member, today_local() - timedelta(days=1), ExitReason.AUSTRITT, recorded_by=senior)
    db.session.commit()
    reactivate_member(member, actor=senior)
    db.session.commit()
    member = reload(member)
    assert (member.status, member.ended_on, member.ended_reason) == (MemberStatus.ACTIVE, None, None)
    assert current_rank(member) is None and office_holders(office_by_key('schriftwart')) == []
    (entry,) = audit_entries('members.reactivate')
    assert entry.actor_user_id == senior.id
    assert entry.diff == {'ended_on': (today_local() - timedelta(days=1)).isoformat(), 'reason': 'austritt'}
    with pytest.raises(MemberError, match='aktives Mitglied'):
        reactivate_member(member, actor=senior)


def test_former_members_cannot_join_groups(app):
    member = make_member('Ex Mitglied', status=MemberStatus.ENDED, ended_on=date(2025, 3, 31))
    with pytest.raises(OrgError, match='kein aktives Mitglied'):
        add_to_group(create_group('Küche A'), member, today_local())


# --- Austritt and Wiederaufnahme (pages) -----------------------------------------


def test_exit_page(client, login, club):
    member = club['member']
    url = f'/m/{member.public_id}/exit'
    login(club['senior'])
    page = client.get(url).get_data(as_text=True)
    assert f'value="{today_local().isoformat()}"' in page
    assert 'Schriftwart' in page and 'Küche A' in page
    assert all(label in page for label in ExitReason.LABELS.values())

    response = client.post(url, data={'ended_on': today_local().isoformat(), 'reason': 'ausschluss', 'note': 'x'})
    assert response.status_code == 302 and response.location.endswith(f'/m/{member.public_id}')
    page = client.get(response.location).get_data(as_text=True)
    assert 'Austritt von Max Muster' in page
    assert f'Ehemalig seit {today_local():%d.%m.%Y} · Ausschluss' in page
    assert 'Wieder aufnehmen' in page and 'Austritt eintragen' not in page
    assert reload(member).status == MemberStatus.ENDED
    assert len(notifications(club['kassenwart'].member)) == 1

    # A second exit is not possible.
    response = client.get(url, follow_redirects=True)
    assert 'bereits ausgeschieden' in response.get_data(as_text=True)


def test_exit_page_rejects_bad_input(client, login, club):
    member = club['member']
    login(club['senior'])
    url = f'/m/{member.public_id}/exit'
    tomorrow = (today_local() + timedelta(days=1)).isoformat()
    page = client.post(url, data={'ended_on': tomorrow, 'reason': 'austritt'}).get_data(as_text=True)
    assert 'nicht in der Zukunft' in page
    response = client.post(url, data={'ended_on': today_local().isoformat(), 'reason': 'weggelaufen'})
    assert response.status_code == 200
    assert reload(member).status == MemberStatus.ACTIVE
    assert audit_entries('members.exit') == []


def test_exit_without_kassenwart_warns(client, login):
    target = make_user(email='target@example.org', name='Tim Target').member
    login(make_officer('senior'))
    response = client.post(
        f'/m/{target.public_id}/exit',
        data={'ended_on': today_local().isoformat(), 'reason': 'austritt'},
        follow_redirects=True,
    )
    assert 'Kein Kassenwart eingetragen' in response.get_data(as_text=True)
    assert reload(target).status == MemberStatus.ENDED


def test_own_exit_is_refused(client, login):
    senior = make_officer('senior')
    login(senior)
    url = f'/m/{senior.member.public_id}/exit'
    assert 'Den eigenen Austritt kann man nicht selbst eintragen' in client.get(url).get_data(as_text=True)
    page = client.post(url, data={'ended_on': today_local().isoformat(), 'reason': 'austritt'}).get_data(as_text=True)
    assert 'Den eigenen Austritt kann man nicht selbst eintragen' in page
    assert reload(senior.member).status == MemberStatus.ACTIVE


def test_exit_and_reactivate_need_exit_record(client, login):
    target = make_user(email='target@example.org', name='Tim Target').member
    former = make_member('Ex Mitglied', status=MemberStatus.ENDED, ended_on=date(2025, 3, 31))
    # members.edit_any alone is not enough.
    login(
        make_user(email='editor@example.org', name='Edi Editor', perms=['members.directory.view', 'members.edit_any'])
    )
    assert client.get(f'/m/{target.public_id}/exit').status_code == 403
    assert client.post(f'/m/{target.public_id}/exit', data={'reason': 'austritt'}).status_code == 403
    assert client.post(f'/m/{former.public_id}/reactivate').status_code == 403
    page = client.get(f'/m/{target.public_id}').get_data(as_text=True)
    assert 'Austritt eintragen' not in page and 'Login sperren' in page
    assert reload(target).status == MemberStatus.ACTIVE and reload(former).status == MemberStatus.ENDED


def test_former_member_can_log_in_but_only_sees_the_own_profile(app, client, login, club):
    max_ = club['max']
    exit_member(club['member'], today_local(), ExitReason.AUSTRITT, recorded_by=club['senior'])
    db.session.commit()
    login(max_)
    assert is_logged_in(client)
    page = client.get('/profile').get_data(as_text=True)
    assert f'Ehemaliges Mitglied seit {today_local():%d.%m.%Y}' in page
    assert 'Bursch' in page and f'bis {today_local():%d.%m.%Y}' in page  # the last rank
    assert client.get('/members').status_code == 403
    assert client.get(f'/m/{club["senior"].member.public_id}').status_code == 403
    assert client.get('/notifications').status_code == 200


def test_reactivate_page(client, login, club):
    member = club['member']
    exit_member(member, today_local(), ExitReason.AUSTRITT, recorded_by=club['senior'])
    db.session.commit()
    login(club['senior'])
    response = client.post(f'/m/{member.public_id}/reactivate', follow_redirects=True)
    page = response.get_data(as_text=True)
    assert 'Max Muster ist wieder aktives Mitglied' in page and 'Bitte den Rang neu setzen' in page
    assert 'Austritt eintragen' in page and 'Ehemalig' not in page
    assert reload(member).status == MemberStatus.ACTIVE

    response = client.post(f'/m/{member.public_id}/reactivate', follow_redirects=True)
    assert 'ist aktives Mitglied' in response.get_data(as_text=True)
    assert len(audit_entries('members.reactivate')) == 1


def test_exit_record_holders_see_former_members(client, login):
    former = make_member('Ex Mitglied', status=MemberStatus.ENDED, ended_on=date(2025, 3, 31))
    login(
        make_user(
            email='sw@example.org', name='Sabine Schrift', perms=['members.directory.view', 'members.exit.record']
        )
    )
    assert 'Ex Mitglied' in client.get('/members?ended=1').get_data(as_text=True)
    page = client.get(f'/m/{former.public_id}').get_data(as_text=True)
    assert 'Ehemalig seit 31.03.2025' in page and 'Wieder aufnehmen' in page
    assert 'Login sperren' not in page and 'Kein Login' not in page  # login management needs members.edit_any


def test_member_page_lifecycle_buttons_follow_permissions(client, login):
    target = make_user(email='target@example.org', name='Tim Target').member
    url = f'/m/{target.public_id}'
    viewer = make_user(email='bursch@example.org', name='Bernd Bursch')
    login(viewer)
    page = client.get(url).get_data(as_text=True)
    # id="member-admin" is the page's "Verwaltung" section (the menu has a "Verwaltung" group too).
    for text in ('id="member-admin"', 'Login sperren', 'Überall abmelden', 'Austritt eintragen', 'Login: '):
        assert text not in page
    client.post('/logout')

    senior = make_officer('senior')
    login(senior)
    page = client.get(url).get_data(as_text=True)
    assert 'id="member-admin"' in page and 'Login: target@example.org · aktiv' in page
    assert f'/m/{target.public_id}/login/disable' in page and f'/m/{target.public_id}/logout-everywhere' in page
    assert f'/m/{target.public_id}/exit' in page
    # The own page has none of these; the own logout is on /profile.
    page = client.get(f'/m/{senior.member.public_id}').get_data(as_text=True)
    assert 'id="member-admin"' not in page and 'Austritt eintragen' not in page

    target.user.is_enabled = False
    db.session.commit()
    page = client.get(url).get_data(as_text=True)
    assert 'gesperrt' in page and f'/m/{target.public_id}/login/enable' in page
    assert 'Login sperren' not in page


def test_former_members_are_not_offered_for_offices_and_groups(client, login):
    make_member('Ex Mitglied', status=MemberStatus.ENDED, ended_on=date(2025, 3, 31))
    group = create_group('Küche A')
    db.session.commit()
    login(make_officer('senior'))
    assert 'Ex Mitglied' not in client.get('/org/offices/kassenwart/assign').get_data(as_text=True)
    assert 'Ex Mitglied' not in client.get(f'/org/groups/{group.id}').get_data(as_text=True)
