from datetime import date

import pytest
from sqlalchemy import select

from community.core.identity.models import MemberStatus
from community.core.org.services import add_to_group, assign_office, create_group, office_by_key
from community.core.platform.audit import AuditLog
from community.core.time import utcnow
from community.extensions import db
from community.modules.members.services import (
    EmailInUse,
    MemberError,
    contact_email,
    directory,
    overview,
    update_member,
    update_own_profile,
    visible_contact,
)

from .factories import give_permissions, make_member, make_officer, make_user, set_rank

SINCE = date(2020, 1, 1)


def audit_entries(action):
    return db.session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)).all()


@pytest.fixture
def people(app):
    """A small club: two Burschen (one Kassenwart), a Fux, a member without rank and a former member."""
    zoe = make_member('Zoe Zett', biername='Zebra')
    set_rank(zoe, 'bursch')
    assign_office(office_by_key('kassenwart'), zoe, SINCE)
    anna = make_member('Anna Alpha')
    set_rank(anna, 'fux')
    oender = make_member('Önder Öz', biername='Bärchen')
    bert = make_member('bert Bursch')
    set_rank(bert, 'bursch')
    ex = make_member('Ex Mitglied', status=MemberStatus.ENDED, ended_on=date(2025, 3, 31))
    db.session.commit()
    return {'zoe': zoe, 'anna': anna, 'oender': oender, 'bert': bert, 'ex': ex}


def names(rows):
    return [row.member.first_name for row in rows]


# --- Directory -------------------------------------------------------------------


def test_directory_lists_active_members_by_name_with_rank_and_offices(people):
    rows = directory()
    # Case and umlauts do not change the order (Önder sorts with O); former members are left out.
    assert names(rows) == ['Anna', 'bert', 'Önder', 'Zoe']
    zoe = rows[-1]
    assert zoe.rank_entry.rank.key == 'bursch'
    assert [a.office.key for a in zoe.offices] == ['kassenwart']
    assert rows[2].rank_entry is None and rows[2].offices == []


@pytest.mark.parametrize(
    ('q', 'expected'),
    [
        ('anna', ['Anna']),
        ('ALPHA', ['Anna']),
        ('anna alpha', ['Anna']),
        ('alpha anna', ['Anna']),
        ('önder', ['Önder']),
        ('ÖZ', ['Önder']),
        ('onder', ['Önder']),
        ('barchen', ['Önder']),
        ('zebra', ['Zoe']),
        ('z', ['Önder', 'Zoe']),
        ('nobody', []),
        ('', ['Anna', 'bert', 'Önder', 'Zoe']),
    ],
)
def test_directory_search_by_name_or_biername(people, q, expected):
    assert names(directory(q=q)) == expected


def test_directory_rank_filter(people):
    assert names(directory(rank_key='bursch')) == ['bert', 'Zoe']
    assert names(directory(rank_key='fux')) == ['Anna']
    assert names(directory(rank_key='alter_herr')) == []
    assert names(directory(q='zoe', rank_key='fux')) == []


def test_directory_former_members_only_on_request(people):
    assert 'Ex' not in names(directory())
    assert 'Ex' in names(directory(include_ended=True))


def test_overview_skips_archived_groups(people):
    zoe = people['zoe']
    kitchen = create_group('Küche A')
    old = create_group('Alte Gruppe')
    add_to_group(kitchen, zoe, SINCE, is_lead=True)
    add_to_group(old, zoe, SINCE)
    old.archived_at = utcnow()
    db.session.commit()
    result = overview(zoe)
    assert result.rank_entry.rank.key == 'bursch' and result.rank_entry.valid_from == date(2000, 1, 1)
    assert [m.group.name for m in result.groups] == ['Küche A']
    assert [a.office.key for a in result.offices] == ['kassenwart']


# --- Contact data ----------------------------------------------------------------


@pytest.mark.parametrize(
    ('permissions', 'show_phone', 'show_address', 'visible'),
    [
        (set(), True, True, set()),
        ({'members.directory.view'}, True, True, set()),
        ({'members.contact.view'}, False, False, {'email'}),
        ({'members.contact.view'}, True, False, {'email', 'phone'}),
        ({'members.contact.view'}, False, True, {'email', 'address'}),
        ({'members.contact.view'}, True, True, {'email', 'phone', 'address'}),
        ({'members.edit_any'}, False, False, {'email', 'phone', 'address'}),
    ],
)
def test_contact_visibility(app, permissions, show_phone, show_address, visible):
    member = make_member(
        'Kai Kontakt',
        email='kai@example.org',
        phone='0171 123',
        address='Hauptstr. 1',
        show_phone=show_phone,
        show_address=show_address,
    )
    shown = visible_contact(member, permissions)
    assert {field for field, value in shown.items() if value is not None} == visible


def test_own_contact_data_is_always_visible(app):
    member = make_member('Kai Kontakt', email='kai@example.org', phone='0171 123', address='Hauptstr. 1')
    assert visible_contact(member, set(), is_self=True) == {
        'email': 'kai@example.org',
        'phone': '0171 123',
        'address': 'Hauptstr. 1',
    }


def test_contact_email_falls_back_to_login_email(app):
    user = make_user(email='login@example.org')
    assert contact_email(user.member) == 'login@example.org'
    user.member.email = None
    assert contact_email(user.member) == 'login@example.org'
    user.member.email = 'contact@example.org'
    assert contact_email(user.member) == 'contact@example.org'
    assert contact_email(make_member('No Mail')) is None


# --- Editing ---------------------------------------------------------------------


def test_update_member_normalises_and_audits_the_diff(app):
    admin = make_user(email='admin@example.org', name='Ad Min')
    member = make_member('Max Muster', email='max@example.org', phone='0171')
    diff = update_member(
        member,
        {'first_name': ' Maximilian ', 'email': ' New@Example.ORG ', 'phone': '', 'biername': 'Bier'},
        actor=admin,
    )
    db.session.commit()
    assert (member.first_name, member.email, member.phone, member.biername) == (
        'Maximilian',
        'new@example.org',
        None,
        'Bier',
    )
    assert diff == {
        'first_name': ['Max', 'Maximilian'],
        'email': ['max@example.org', 'new@example.org'],
        'phone': ['0171', None],
        'biername': [None, 'Bier'],
    }
    (entry,) = audit_entries('members.update')
    assert entry.actor_user_id == admin.id
    assert entry.entity_type == 'member' and entry.entity_id == str(member.id)
    assert entry.diff['email'] == ['max@example.org', 'new@example.org']
    assert set(entry.diff) == {'first_name', 'email', 'phone', 'biername'}


def test_update_member_without_changes_is_not_audited(app):
    member = make_member('Max Muster', email='max@example.org')
    assert update_member(member, {'first_name': 'Max', 'email': 'MAX@example.org', 'show_phone': False}) == {}
    assert audit_entries('members.update') == []


def test_update_member_rejects_email_of_another_member(app):
    make_member('Other Contact', email='taken@example.org')
    make_user(email='login@example.org', name='Other Login')
    member = make_user(email='me@example.org', name='Me Myself').member
    with pytest.raises(EmailInUse):
        update_member(member, {'email': 'Taken@example.org'})
    with pytest.raises(EmailInUse):
        update_member(member, {'email': 'login@example.org'})
    db.session.rollback()
    # The member's own login email is fine as contact email.
    member.email = None
    update_member(member, {'email': 'me@example.org'})
    assert member.email == 'me@example.org'


def test_update_member_needs_a_first_name(app):
    member = make_member('Max Muster')
    with pytest.raises(MemberError, match='Vorname'):
        update_member(member, {'first_name': '  '})


def test_own_profile_cannot_change_names_or_email(app):
    member = make_member('Max Muster', email='max@example.org')
    for field in ('first_name', 'last_name', 'email', 'joined_on'):
        with pytest.raises(ValueError, match=field):
            update_own_profile(member, {field: 'x'})
    diff = update_own_profile(member, {'biername': 'Bier', 'phone': '0171', 'show_phone': True})
    db.session.commit()
    assert diff == {'biername': [None, 'Bier'], 'phone': [None, '0171'], 'show_phone': [False, True]}
    assert [e.diff for e in audit_entries('members.profile.update')] == [diff]


# --- Pages -----------------------------------------------------------------------


def test_directory_needs_permission(client, login):
    make_user(perms=[])
    login('max@example.org')
    assert client.get('/members').status_code == 403
    assert client.get('/profile').status_code == 200


def test_directory_page_search_and_rank_filter(client, login, people):
    login(make_user(email='viewer@example.org', name='Vera Viewer'))
    page = client.get('/members?q=alpha').get_data(as_text=True)
    assert 'Anna Alpha' in page and 'Zoe Zett' not in page
    page = client.get('/members?rank=fux').get_data(as_text=True)
    assert 'Anna Alpha' in page and 'Zoe Zett' not in page
    page = client.get('/members?rank=unknown').get_data(as_text=True)
    assert 'Anna Alpha' in page and 'Zoe Zett' in page
    page = client.get('/members').get_data(as_text=True)
    assert 'Kassenwart' in page and 'Fux' in page
    assert f'/m/{people["zoe"].public_id}' in page


def test_former_members_only_for_edit_any(client, login, people):
    login(make_user(email='viewer@example.org', name='Vera Viewer'))
    page = client.get('/members?ended=1').get_data(as_text=True)
    assert 'Ex Mitglied' not in page and 'Ehemalige' not in page
    assert client.get(f'/m/{people["ex"].public_id}').status_code == 404
    client.post('/logout')

    login(
        make_user(email='editor@example.org', name='Edi Editor', perms=['members.directory.view', 'members.edit_any'])
    )
    assert 'Ex Mitglied' not in client.get('/members').get_data(as_text=True)
    page = client.get('/members?ended=1').get_data(as_text=True)
    assert 'Ehemalige' in page and 'Ex Mitglied' in page and 'ehemalig seit 31.03.2025' in page
    assert client.get(f'/m/{people["ex"].public_id}').status_code == 200


def test_member_page(client, login, people):
    zoe = people['zoe']
    zoe.joined_on = date(2019, 10, 15)
    group = create_group('Küche A')
    add_to_group(group, zoe, SINCE)
    db.session.commit()
    login(make_user(email='viewer@example.org', name='Vera Viewer', rank='fux'))
    page = client.get(f'/m/{zoe.public_id}').get_data(as_text=True)
    assert 'Zoe Zett' in page and 'Zebra' in page
    assert 'Bursch' in page and 'seit 01.01.2000' in page
    assert 'Kassenwart' in page and 'Küche A' in page and '15.10.2019' in page
    assert zoe.payment_ref not in page
    assert client.get('/m/does-not-exist').status_code == 404


def test_member_page_contact_data_by_permission(client, login):
    target = make_member(
        'Kai Kontakt',
        email='kai@example.org',
        phone='0171 555',
        address='Geheimweg 7',
        show_phone=True,
        show_address=False,
    )
    url = f'/m/{target.public_id}'

    login(make_user(email='fux@example.org', name='Felix Fux', rank='fux'))  # directory only
    page = client.get(url).get_data(as_text=True)
    assert 'kai@example.org' not in page and '0171 555' not in page and 'Geheimweg' not in page
    assert 'Keine Kontaktdaten sichtbar' in page
    client.post('/logout')

    login(make_user(email='bursch@example.org', name='Bernd Bursch'))  # contact.view
    page = client.get(url).get_data(as_text=True)
    assert 'kai@example.org' in page and '0171 555' in page and 'Geheimweg' not in page
    client.post('/logout')

    login(
        make_user(email='editor@example.org', name='Edi Editor', perms=['members.directory.view', 'members.edit_any'])
    )
    page = client.get(url).get_data(as_text=True)
    assert 'kai@example.org' in page and '0171 555' in page and 'Geheimweg 7' in page
    assert 'für andere nicht sichtbar' in page


def test_member_page_buttons_follow_permissions(client, login):
    target = make_member('Tim Target')
    url = f'/m/{target.public_id}'
    bursch = make_user(email='bursch@example.org', name='Bernd Bursch')
    login(bursch)
    page = client.get(url).get_data(as_text=True)
    assert 'Bearbeiten' not in page and 'Rang ändern' not in page
    client.post('/logout')

    senior = make_officer('senior')
    login(senior)
    page = client.get(url).get_data(as_text=True)
    assert f'/m/{target.public_id}/edit' in page and f'/org/members/{target.public_id}/rank' in page
    # Nobody changes their own rank, so the own page has no such button.
    page = client.get(f'/m/{senior.member.public_id}').get_data(as_text=True)
    assert 'Rang ändern' not in page and 'Mein Profil' in page


def test_edit_member(client, login):
    target = make_member('Tim Target', email='tim@example.org')
    url = f'/m/{target.public_id}/edit'
    login(make_user(email='bursch@example.org', name='Bernd Bursch'))
    assert client.get(url).status_code == 403
    assert client.post(url, data={'first_name': 'Hacked'}).status_code == 403
    client.post('/logout')

    editor = make_user(
        email='editor@example.org', name='Edi Editor', perms=['members.directory.view', 'members.edit_any']
    )
    login(editor)
    assert 'tim@example.org' in client.get(url).get_data(as_text=True)
    response = client.post(
        url,
        data={
            'first_name': 'Timo',
            'last_name': 'Target',
            'biername': 'Tiger',
            'email': 'TIMO@example.org',
            'phone': '+49 171 1',
            'address': '',
            'joined_on': '2021-04-01',
            'show_phone': 'y',
        },
    )
    assert response.status_code == 302
    assert response.location.endswith(f'/m/{target.public_id}')
    db.session.refresh(target)
    assert (target.first_name, target.biername, target.email, target.phone) == (
        'Timo',
        'Tiger',
        'timo@example.org',
        '+49 171 1',
    )
    assert target.joined_on == date(2021, 4, 1) and target.show_phone and not target.show_address
    (entry,) = audit_entries('members.update')
    assert entry.actor_user_id == editor.id
    assert entry.diff['first_name'] == ['Tim', 'Timo'] and entry.diff['joined_on'] == [None, '2021-04-01']
    assert 'last_name' not in entry.diff


def test_edit_member_duplicate_email_is_shown_on_the_form(client, login):
    make_member('Other Person', email='taken@example.org')
    target = make_member('Tim Target', email='tim@example.org')
    login(
        make_user(email='editor@example.org', name='Edi Editor', perms=['members.directory.view', 'members.edit_any'])
    )
    response = client.post(f'/m/{target.public_id}/edit', data={'first_name': 'Tim', 'email': 'Taken@Example.org'})
    assert response.status_code == 200
    assert 'gehört schon zu einem anderen Mitglied' in response.get_data(as_text=True)
    db.session.refresh(target)
    assert target.email == 'tim@example.org'
    assert audit_entries('members.update') == []


def test_edit_member_validates_phone(client, login):
    target = make_member('Tim Target')
    login(make_user(email='editor@example.org', name='Edi Editor', perms=['members.edit_any']))
    response = client.post(f'/m/{target.public_id}/edit', data={'first_name': 'Tim', 'phone': 'call me'})
    assert response.status_code == 200
    assert 'Nur Ziffern' in response.get_data(as_text=True)


def test_profile_page_shows_own_data(client, login):
    user = make_officer('schriftwart', email='me@example.org', name='Max Muster')
    group = create_group('Küche A')
    add_to_group(group, user.member, SINCE)
    db.session.commit()
    login(user)
    page = client.get('/profile').get_data(as_text=True)
    assert 'Max Muster' in page and 'Bursch' in page and 'Schriftwart' in page and 'Küche A' in page
    assert 'me@example.org' in page
    assert '/settings/notifications' in page
    assert user.member.payment_ref not in page


def test_profile_edit_cannot_change_names_or_email(client, login):
    user = make_user(email='me@example.org', name='Max Muster')
    login(user)
    response = client.post(
        '/profile',
        data={
            'first_name': 'Hacker',
            'last_name': 'Evil',
            'email': 'evil@example.org',
            'biername': 'Bierkönig',
            'phone': '0171 2',
            'address': 'Haus 1',
            'show_address': 'y',
        },
    )
    assert response.status_code == 302
    member = user.member
    db.session.refresh(member)
    assert (member.first_name, member.last_name, member.email) == ('Max', 'Muster', 'me@example.org')
    assert (member.biername, member.phone, member.address) == ('Bierkönig', '0171 2', 'Haus 1')
    assert member.show_address and not member.show_phone
    (entry,) = audit_entries('members.profile.update')
    assert set(entry.diff) == {'biername', 'phone', 'address', 'show_address'}


def test_navigation_links_to_directory_and_profile(client, login):
    login(make_user(email='me@example.org', name='Max Muster', rank='fux'))
    page = client.get('/').get_data(as_text=True)
    assert 'href="/members"' in page and 'href="/profile"' in page and '>Max</a>' in page
    client.post('/logout')

    login(make_user(email='new@example.org', name='Nina Neu', perms=[]))
    page = client.get('/').get_data(as_text=True)
    assert 'href="/members"' not in page and 'href="/profile"' in page


def test_directory_view_permission_alone_is_enough(client, login, people):
    user = make_user(email='viewer@example.org', name='Vera Viewer', perms=[])
    give_permissions(user.member, 'members.directory.view')
    login(user)
    assert client.get('/members').status_code == 200
    page = client.get(f'/m/{people["zoe"].public_id}').get_data(as_text=True)
    assert 'Bearbeiten' not in page
