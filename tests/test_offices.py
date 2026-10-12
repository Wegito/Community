from datetime import date, timedelta

import pytest
from sqlalchemy import select

from community.core.authz.service import active_role_ids, active_roles, permissions_for, role_by_key
from community.core.org.models import GroupMembership, MemberGroup, OfficeAssignment
from community.core.org.services import (
    OrgError,
    add_to_group,
    assign_office,
    change_rank,
    create_group,
    end_assignment,
    end_membership,
    group_members,
    member_offices,
    office_by_key,
    office_holders,
    rank_by_key,
)
from community.core.time import today_local
from community.extensions import db

from .factories import make_member, make_officer, make_user, set_rank


def bursch(name='Bernd Bursch', email=None):
    member = make_member(name, email=email)
    set_rank(member, 'bursch')
    return member


def test_six_offices_are_seeded(app):
    keys = [o.key for o in db.session.scalars(select(office_by_key('senior').__class__))]
    assert set(keys) == {'senior', 'consenior', 'schriftwart', 'kassenwart', 'fuxmajor', 'bierwart'}


def test_office_gives_its_permissions(app):
    senior = make_officer('senior')
    assert {'tasks.manage', 'members.rank.change', 'offices.assign'} <= permissions_for(senior.member)
    plain = make_user(email='plain@example.org', name='Plain Bursch')
    assert 'tasks.manage' not in permissions_for(plain.member)


def test_only_burschen_unless_acting(app):
    fux = make_member('Felix Fux')
    set_rank(fux, 'fux')
    with pytest.raises(OrgError, match='kommissarisch'):
        assign_office(office_by_key('bierwart'), fux, date(2026, 10, 1))
    assignment = assign_office(office_by_key('bierwart'), fux, date(2026, 10, 1), is_acting=True)
    assert assignment.is_acting


def test_nobody_assigns_themselves(app):
    senior = make_officer('senior')
    with pytest.raises(OrgError, match='selbst'):
        assign_office(office_by_key('kassenwart'), senior.member, date(2026, 10, 1), assigned_by=senior)


def test_kassenwart_and_bierwart_are_incompatible(app):
    member = bursch()
    assign_office(office_by_key('kassenwart'), member, date(2026, 10, 1))
    with pytest.raises(OrgError, match='gleichzeitig'):
        assign_office(office_by_key('bierwart'), member, date(2026, 11, 1))
    # Two compatible Ämter are fine.
    assign_office(office_by_key('schriftwart'), member, date(2026, 11, 1))
    assert len(member_offices(member, date(2026, 11, 2))) == 2


def test_successor_ends_previous_term(app):
    old, new = bursch('Old Holder'), bursch('New Holder')
    kw = office_by_key('kassenwart')
    first = assign_office(kw, old, date(2026, 4, 1))
    with pytest.raises(OrgError, match='besetzt'):
        assign_office(kw, new, date(2026, 10, 1), end_previous=False)
    assign_office(kw, new, date(2026, 10, 1))
    assert first.valid_to == date(2026, 9, 30)
    assert [a.member_id for a in office_holders(kw, date(2026, 9, 30))] == [old.id]
    assert [a.member_id for a in office_holders(kw, date(2026, 10, 1))] == [new.id]


def test_cannot_overwrite_a_later_assignment(app):
    a, b = bursch('A Person'), bursch('B Person')
    kw = office_by_key('kassenwart')
    assign_office(kw, a, date(2027, 4, 1))
    with pytest.raises(OrgError, match='ab 01.04.2027'):
        assign_office(kw, b, date(2026, 10, 1))


def test_end_assignment_removes_rights_after_the_last_day(app):
    officer = make_officer('consenior')
    assignment = member_offices(officer.member)[0]
    end_assignment(assignment, date(2025, 12, 31), reason='Rücktritt')
    db.session.commit()
    assert 'tasks.manage' not in permissions_for(officer.member)
    assert 'tasks.manage' in permissions_for(officer.member, date(2025, 12, 31))
    with pytest.raises(OrgError, match='vor dem Beginn'):
        end_assignment(assignment, date(2019, 1, 1))


@pytest.mark.parametrize(('choice', 'still_holds', 'acting'), [('close', False, None), ('keep_acting', True, True)])
def test_rank_change_handles_ineligible_offices(app, choice, still_holds, acting):
    officer = make_officer('senior')
    change_rank(officer.member, rank_by_key('alter_herr'), date(2026, 10, 1), offices=choice)
    db.session.commit()
    held = member_offices(officer.member, date(2026, 10, 1))
    assert bool(held) is still_holds
    if acting:
        assert held[0].is_acting


def test_groups_and_hausbewohner_role(app):
    member = bursch()
    hausbewohner = db.session.scalar(select(MemberGroup).where(MemberGroup.name == 'Hausbewohner'))
    membership = add_to_group(hausbewohner, member, date(2026, 1, 1))
    assert role_by_key('group.hausbewohner').id in active_role_ids(member)
    with pytest.raises(OrgError, match='bereits'):
        add_to_group(hausbewohner, member, date(2026, 2, 1))
    end_membership(membership, date(2026, 3, 1))
    assert role_by_key('group.hausbewohner').id not in active_role_ids(member, date(2026, 3, 2))
    team = create_group('Küche A')
    with pytest.raises(OrgError, match='gibt es schon'):
        create_group('Küche A')
    add_to_group(team, member, date(2026, 1, 1), is_lead=True)
    assert group_members(team, date(2026, 1, 1))[0].is_lead


def test_offices_pages(client, login):
    senior = make_officer('senior')
    target = bursch('Kai Kasse', email='kai@example.org')
    login(senior)
    assert 'Kassenwart' in client.get('/org/offices').get_data(as_text=True)
    response = client.post(
        '/org/offices/kassenwart/assign',
        data={'member_id': target.id, 'valid_from': '2026-10-01', 'semester_id': 'None', 'end_previous': 'y'},
    )
    assert response.status_code == 302
    page = client.get('/org/offices/kassenwart').get_data(as_text=True)
    assert 'Kai Kasse' in page
    client.post('/org/offices/kassenwart', data={'description_md': 'Neue Übergabe-Notiz'})
    assert office_by_key('kassenwart').description_md == 'Neue Übergabe-Notiz'
    assignment = db.session.scalar(select(OfficeAssignment).where(OfficeAssignment.member_id == target.id))
    client.post(f'/org/offices/assignments/{assignment.id}/end', data={'valid_to': '2027-03-31', 'reason': 'Ende'})
    db.session.refresh(assignment)
    assert assignment.valid_to == date(2027, 3, 31)


def test_self_assignment_through_the_page_is_refused(client, login):
    senior = make_officer('senior')
    login(senior)
    response = client.post(
        '/org/offices/kassenwart/assign',
        data={'member_id': senior.member_id, 'valid_from': '2026-10-01', 'semester_id': 'None', 'end_previous': 'y'},
    )
    assert response.status_code == 200
    assert 'selbst' in response.get_data(as_text=True)


def test_description_edit_needs_offices_assign(client, login):
    user = make_user()
    login(user)
    assert client.post('/org/offices/senior', data={'description_md': 'x'}).status_code == 403


def test_groups_pages(client, login):
    consenior = make_officer('consenior')
    target = bursch('Gina Gruppe')
    login(consenior)
    client.post('/org/groups', data={'name': 'Küche A', 'kind': 'task'})
    team = db.session.scalar(select(MemberGroup).where(MemberGroup.name == 'Küche A'))
    client.post(f'/org/groups/{team.id}', data={'member_id': target.id, 'valid_from': '2026-10-01'})
    assert 'Gina Gruppe' in client.get(f'/org/groups/{team.id}').get_data(as_text=True)
    membership = group_members(team, date(2026, 10, 1))[0]
    client.post(f'/org/groups/memberships/{membership.id}/end')
    db.session.refresh(membership)
    assert membership.valid_to is not None


# --- Ämter that start later (review findings 1, 10, 19) ---------------------------


def audit_actions(action):
    from community.core.platform.audit import AuditLog

    return db.session.scalars(select(AuditLog).where(AuditLog.action == action)).all()


def test_rank_change_closes_offices_that_start_later(app):
    member = bursch('Xaver Elect')
    start = today_local() + timedelta(days=30)
    assign_office(office_by_key('senior'), member, start)
    db.session.commit()
    change_rank(member, rank_by_key('alter_herr'), today_local(), offices='close')
    db.session.commit()
    # The term never takes effect: no one-day term on its first day, and nothing in the history.
    assert member_offices(member, start) == []
    assert db.session.scalars(select(OfficeAssignment).where(OfficeAssignment.member_id == member.id)).all() == []
    assert 'offices.assign' not in permissions_for(member, start)
    (entry,) = audit_actions('org.office.cancel')
    assert entry.diff['office'] == 'senior' and entry.diff['valid_from'] == start.isoformat()


def test_rank_change_keeps_offices_that_start_later_as_acting(app):
    member = bursch('Xaver Elect')
    start = today_local() + timedelta(days=30)
    assign_office(office_by_key('senior'), member, start)
    change_rank(member, rank_by_key('alter_herr'), today_local() + timedelta(days=1), offices='keep_acting')
    db.session.commit()
    (held,) = member_offices(member, start)
    assert held.is_acting
    assert [e.diff['office'] for e in audit_actions('org.office.keep_acting')] == ['senior']


def test_backdated_rank_change_closes_offices_that_started_after_it(app):
    member = bursch('Bernd Bursch')
    assign_office(office_by_key('kassenwart'), member, today_local() - timedelta(days=10))
    db.session.commit()
    change_rank(member, rank_by_key('alter_herr'), today_local() - timedelta(days=20), offices='close')
    db.session.commit()
    assert member_offices(member) == []
    assert 'office.kassenwart' not in {r.key for r in active_roles(member)}


def test_office_needs_an_eligible_rank_for_the_whole_term(app):
    member = bursch('Bernd Bursch')
    change_rank(member, rank_by_key('alter_herr'), today_local() + timedelta(days=60))
    db.session.commit()
    later = (today_local() + timedelta(days=60)).strftime('%d.%m.%Y')
    with pytest.raises(OrgError, match=f'ab {later} Alter Herr'):
        assign_office(office_by_key('kassenwart'), member, today_local())
    # A term that ends before the new rank starts is a later question; acting is the audited exception.
    assignment = assign_office(office_by_key('kassenwart'), member, today_local(), is_acting=True)
    assert assignment.is_acting
    (entry,) = audit_actions('org.office.assign')
    assert entry.diff['eligible'] is False


def test_rank_page_lists_offices_that_start_later(client, login):
    senior = make_officer('senior')
    target = bursch('Xaver Elect')
    start = today_local() + timedelta(days=30)
    assign_office(office_by_key('consenior'), target, start)
    db.session.commit()
    login(senior)
    page = client.get(f'/org/members/{target.public_id}/rank').get_data(as_text=True)
    assert 'Hat Ämter' in page and f'Consenior (ab {start:%d.%m.%Y})' in page


# --- Ending now and wrong entries (review findings 13, 26, 27) --------------------


def test_wrong_office_holder_can_be_corrected_the_same_day(client, login):
    senior = make_officer('senior')
    wrong, right = bursch('Bernd Falsch'), bursch('Felix Richtig')
    login(senior)
    data = {'valid_from': today_local().isoformat(), 'semester_id': 'None', 'end_previous': 'y'}
    assert client.post('/org/offices/bierwart/assign', data={**data, 'member_id': wrong.id}).status_code == 302
    (assignment,) = office_holders(office_by_key('bierwart'))
    page = client.get('/org/offices/bierwart').get_data(as_text=True)
    assert f'/org/offices/assignments/{assignment.id}/cancel' in page
    response = client.post(f'/org/offices/assignments/{assignment.id}/cancel')
    assert response.status_code == 302
    assert office_holders(office_by_key('bierwart')) == []
    assert db.session.scalar(select(OfficeAssignment).where(OfficeAssignment.id == assignment.id)) is None
    assert 'office.bierwart' not in {r.key for r in active_roles(wrong)}
    (entry,) = audit_actions('org.office.cancel')
    assert entry.actor_user_id == senior.id and entry.diff['member_id'] == wrong.id
    assert client.post('/org/offices/bierwart/assign', data={**data, 'member_id': right.id}).status_code == 302
    db.session.expire_all()  # SQLite may give the new row the deleted row's id
    assert [a.member_id for a in office_holders(office_by_key('bierwart'))] == [right.id]


def test_only_terms_that_have_not_begun_can_be_cancelled(app):
    from community.core.org.services import cancel_assignment

    member = bursch()
    running = assign_office(office_by_key('kassenwart'), member, today_local() - timedelta(days=1))
    with pytest.raises(OrgError, match='beenden'):
        cancel_assignment(running)
    upcoming = assign_office(office_by_key('schriftwart'), member, today_local() + timedelta(days=5))
    cancel_assignment(upcoming)
    db.session.commit()
    assert [a.office.key for a in member_offices(member, today_local() + timedelta(days=5))] == ['kassenwart']


def test_removing_from_a_group_takes_effect_at_once(client, login):
    consenior = make_officer('consenior')
    member = bursch('Gina Gruppe')
    hausbewohner = db.session.scalar(select(MemberGroup).where(MemberGroup.name == 'Hausbewohner'))
    membership = add_to_group(hausbewohner, member, date(2026, 1, 1))
    db.session.commit()
    login(consenior)
    url = f'/org/groups/memberships/{membership.id}/end'
    response = client.post(url, follow_redirects=True)
    assert 'Gina Gruppe aus Hausbewohner entfernt' in response.get_data(as_text=True)
    db.session.refresh(membership)
    assert membership.valid_to == today_local() - timedelta(days=1)
    assert 'group.hausbewohner' not in {r.key for r in active_roles(member)}
    assert group_members(hausbewohner) == []
    # A second click changes nothing, and the member can be added again the same day.
    client.post(url)
    assert len(audit_actions('org.group.remove')) == 1
    client.post(
        f'/org/groups/{hausbewohner.id}', data={'member_id': member.id, 'valid_from': today_local().isoformat()}
    )
    assert [m.member_id for m in group_members(hausbewohner)] == [member.id]


def test_group_membership_that_has_not_begun_is_deleted(client, login):
    consenior = make_officer('consenior')
    member = bursch('Gina Gruppe')
    team = create_group('Küche A')
    today = add_to_group(team, member, today_local())
    other = bursch('Otto Später')
    later = add_to_group(team, other, today_local() + timedelta(days=7))
    db.session.commit()
    today_id, later_id = today.id, later.id
    login(consenior)
    page = client.get(f'/org/groups/{team.id}').get_data(as_text=True)
    assert 'Otto Später' in page and f'ab {today_local() + timedelta(days=7):%d.%m.%Y}' in page
    client.post(f'/org/groups/memberships/{today_id}/end')
    client.post(f'/org/groups/memberships/{later_id}/end')
    left = db.session.scalars(select(GroupMembership).where(GroupMembership.id.in_([today_id, later_id])))
    assert left.all() == []
    assert len(audit_actions('org.group.cancel')) == 2
    add_to_group(team, other, today_local())
