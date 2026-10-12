"""M1-7: personal invitations replace the shared invite code (roadmap §3.3)."""

import hashlib
import re
from datetime import date, timedelta

import pytest
from sqlalchemy import select, update

from community.core.authz.service import active_roles, permissions_for
from community.core.identity.models import Invitation, InvitationChannel, InvitationState, Member, MemberStatus, User
from community.core.identity.services import (
    EmailInUse,
    InvitationError,
    accept_invitation,
    create_invitation,
    end_all_sessions,
    find_valid_invitation,
    open_invitations,
    revoke_invitation,
)
from community.core.org.services import current_rank, current_rank_entry
from community.core.platform.audit import AuditLog
from community.core.platform.jobs import Job, run_due_jobs
from community.core.platform.notify import Notification
from community.core.time import today_local, utcnow
from community.extensions import db
from community.modules.members import services as members
from community.modules.members.services import exit_member

from .factories import (
    make_admin,
    make_invitation,
    make_member,
    make_officer,
    make_user,
    set_rank,
)

NEW_PASSWORD = 'ein langes passwort'
LINK_RE = re.compile(r'http://localhost/join/([A-Za-z0-9_-]+)')


def audit_entries(action):
    return db.session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)).all()


def outbox(app):
    return app.extensions.setdefault('mail_outbox', [])


def reload(obj):
    db.session.expire_all()
    return db.session.get(type(obj), obj.id)


def rank_id(key):
    from community.core.org.services import rank_by_key

    return rank_by_key(key).id


def new_member_form(**overrides):
    data = {
        'first_name': 'Neu',
        'last_name': 'Ling',
        'biername': '',
        'email': '',
        'joined_on': today_local().isoformat(),
        'rank_id': rank_id('spefux'),
        'delivery': 'link',
    }
    data.update(overrides)
    return data


def join_form(**overrides):
    data = {'login_email': 'neu@example.org', 'password': NEW_PASSWORD, 'confirm': NEW_PASSWORD}
    data.update(overrides)
    return data


def is_logged_in(client):
    response = client.get('/profile')
    assert response.status_code in (200, 302)
    return response.status_code == 200


# --- Creating invitations (identity service) -------------------------------------


@pytest.mark.parametrize(
    ('channel', 'lifetime'),
    [('link', timedelta(days=14)), ('email', timedelta(days=14)), ('qr', timedelta(hours=24))],
)
def test_create_invitation_per_channel(app, channel, lifetime):
    inviter = make_user()
    member = make_member('Neu Ling')
    before = utcnow()
    invitation, raw_token = create_invitation(member, channel, created_by=inviter, email=' Neu@Example.org ')
    db.session.commit()

    invitation = reload(invitation)
    assert invitation.channel == channel
    assert invitation.email == 'neu@example.org'
    assert invitation.created_by_user_id == inviter.id
    assert before + lifetime <= invitation.expires_at <= utcnow() + lifetime
    assert invitation.state() == InvitationState.OPEN
    # Only the hash is stored; the raw token is 256 random bits.
    assert len(raw_token) >= 43
    assert invitation.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()
    entry = audit_entries('identity.invitation.create')[-1]
    assert entry.actor_user_id == inviter.id
    assert entry.diff['channel'] == channel
    assert raw_token not in (entry.diff_json or '')


def test_lifetimes_come_from_config(app):
    app.config['INVITE_LINK_DAYS'] = 3
    app.config['INVITE_QR_HOURS'] = 2
    link, _ = make_invitation(channel='link')
    qr, _ = make_invitation(make_member('Q R'), channel='qr')
    assert link.expires_at - link.created_at == timedelta(days=3)
    assert qr.expires_at - qr.created_at == timedelta(hours=2)


def test_tokens_are_unique(app):
    tokens = {make_invitation(make_member(f'Person {i}'))[1] for i in range(5)}
    assert len(tokens) == 5


def test_member_with_login_cannot_be_invited(app):
    user = make_user()
    with pytest.raises(InvitationError, match='schon einen Login'):
        create_invitation(user.member, 'link', created_by=None)


def test_former_member_cannot_be_invited(app):
    member = make_member('Ex Mitglied', status=MemberStatus.ENDED, ended_on=date(2025, 1, 1))
    with pytest.raises(InvitationError, match='kein aktives Mitglied'):
        create_invitation(member, 'link', created_by=None)


def test_unknown_channel_is_a_programming_error(app):
    with pytest.raises(ValueError):
        create_invitation(make_member('Neu Ling'), 'pigeon', created_by=None)


def test_new_invitation_revokes_older_open_ones(app):
    member = make_member('Neu Ling')
    first, first_token = make_invitation(member)
    second, second_token = make_invitation(member, channel='qr')
    assert reload(first).state() == InvitationState.REVOKED
    assert find_valid_invitation(first_token) is None
    assert find_valid_invitation(second_token).id == second.id
    assert open_invitations(member) == [reload(second)]
    assert audit_entries('identity.invitation.create')[-1].diff['revoked_ids'] == [first.id]


# --- Finding and accepting ---------------------------------------------------------


def test_find_valid_invitation(app):
    invitation, raw_token = make_invitation()
    assert find_valid_invitation(raw_token).id == invitation.id
    assert find_valid_invitation('') is None
    assert find_valid_invitation(None) is None
    assert find_valid_invitation('x' * 500) is None
    assert find_valid_invitation(raw_token + 'x') is None
    # The hash itself is not a token.
    assert find_valid_invitation(invitation.token_hash) is None


@pytest.mark.parametrize('how', ['expired', 'revoked', 'accepted', 'login_elsewhere', 'member_left'])
def test_unusable_invitations_are_not_found(app, how):
    invitation, raw_token = make_invitation()
    if how == 'expired':
        invitation.expires_at = utcnow() - timedelta(seconds=1)
    elif how == 'revoked':
        revoke_invitation(invitation, revoked_by=None)
    elif how == 'accepted':
        accept_invitation(invitation, 'neu@example.org', NEW_PASSWORD)
    elif how == 'login_elsewhere':
        from community.core.identity.services import create_user

        create_user(invitation.member, 'neu@example.org', NEW_PASSWORD)
    else:
        invitation.member.status = MemberStatus.ENDED
    db.session.commit()
    assert find_valid_invitation(raw_token) is None


def test_accept_invitation_creates_the_login(app):
    invitation, _ = make_invitation(email='neu@example.org')
    user = accept_invitation(invitation, ' Neu@Example.ORG ', NEW_PASSWORD)
    db.session.commit()

    user = reload(user)
    assert user.member_id == invitation.member_id
    assert user.login_email == 'neu@example.org'
    assert user.check_password(NEW_PASSWORD)
    assert reload(invitation).state() == InvitationState.ACCEPTED
    entry = audit_entries('identity.invitation.accept')[-1]
    assert entry.actor_user_id == user.id
    assert entry.diff == {'login_email': 'neu@example.org', 'member_id': invitation.member_id, 'user_id': user.id}


def test_invitation_is_single_use(app):
    invitation, _ = make_invitation()
    accept_invitation(invitation, 'neu@example.org', NEW_PASSWORD)
    db.session.commit()
    with pytest.raises(InvitationError):
        accept_invitation(reload(invitation), 'other@example.org', NEW_PASSWORD)


def test_concurrent_accept_is_stopped_by_the_conditional_update(app):
    """Two submits of the same link: the second request still holds an invitation that looks open,
    but the conditional UPDATE matches nothing once the first one has committed."""
    invitation, _ = make_invitation()
    assert invitation.member.user is None
    db.session.execute(
        update(Invitation)
        .where(Invitation.id == invitation.id)
        .values(accepted_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    assert invitation.accepted_at is None  # the stale object
    with pytest.raises(InvitationError):
        accept_invitation(invitation, 'neu@example.org', NEW_PASSWORD)
    assert db.session.scalar(select(User)) is None


def test_accept_refuses_login_email_of_another_account(app):
    make_user(email='taken@example.org')
    invitation, _ = make_invitation()
    with pytest.raises(EmailInUse):
        accept_invitation(invitation, 'TAKEN@example.org', NEW_PASSWORD)


def test_accept_refuses_contact_email_of_another_member(app):
    make_member('Andere Person', email='other@example.org')
    invitation, _ = make_invitation()
    with pytest.raises(EmailInUse):
        accept_invitation(invitation, 'other@example.org', NEW_PASSWORD)


def test_accept_allows_the_members_own_contact_email(app):
    member = make_member('Neu Ling', email='neu@example.org')
    invitation, _ = make_invitation(member)
    assert accept_invitation(invitation, 'neu@example.org', NEW_PASSWORD).login_email == 'neu@example.org'


def test_revoke_invitation(app):
    admin = make_admin()
    invitation, raw_token = make_invitation()
    revoke_invitation(invitation, revoked_by=admin)
    db.session.commit()
    assert reload(invitation).state() == InvitationState.REVOKED
    assert find_valid_invitation(raw_token) is None
    assert audit_entries('identity.invitation.revoke')[-1].actor_user_id == admin.id
    with pytest.raises(InvitationError, match='bereits widerrufen'):
        revoke_invitation(invitation, revoked_by=admin)


def test_accepted_invitation_cannot_be_revoked(app):
    invitation, _ = make_invitation()
    accept_invitation(invitation, 'neu@example.org', NEW_PASSWORD)
    db.session.commit()
    with pytest.raises(InvitationError, match='angenommen'):
        revoke_invitation(invitation, revoked_by=None)


def test_exit_revokes_open_invitations(app):
    """A link must not start working again after a Wiederaufnahme."""
    admin = make_admin()
    invitation, raw_token = make_invitation()
    exit_member(invitation.member, today_local(), 'austritt', recorded_by=admin)
    db.session.commit()
    assert reload(invitation).state() == InvitationState.REVOKED
    assert audit_entries('members.exit')[-1].diff['ended']['invitations'] == [invitation.id]
    members.reactivate_member(invitation.member, actor=admin)
    db.session.commit()
    assert find_valid_invitation(raw_token) is None


# --- Neues Mitglied (members service) --------------------------------------------


def test_add_member_with_link_invitation(app):
    inviter = make_officer('fuxmajor')
    member, invitation, raw_token = members.add_member(
        {'first_name': ' Neu ', 'last_name': 'Ling', 'biername': 'Küken', 'email': 'Neu@Example.org'},
        members.starting_ranks({'members.invite'})[0],
        'link',
        invited_by=inviter,
        permissions={'members.invite'},
    )
    db.session.commit()

    member = reload(member)
    assert (member.first_name, member.last_name, member.biername, member.email) == (
        'Neu',
        'Ling',
        'Küken',
        'neu@example.org',
    )
    assert member.joined_on == today_local()
    assert member.payment_ref is not None
    assert member.user is None
    entry = current_rank_entry(member)
    assert entry.rank.key == 'spefux'
    assert entry.valid_from == today_local()
    assert entry.changed_by_user_id == inviter.id
    assert find_valid_invitation(raw_token).id == invitation.id
    assert invitation.email == 'neu@example.org'
    assert audit_entries('members.create')[-1].actor_user_id == inviter.id
    assert audit_entries('org.rank.change')[-1].actor_user_id == inviter.id


def test_add_member_without_invitation(app):
    inviter = make_officer('fuxmajor')
    member, invitation, raw_token = members.add_member(
        {'first_name': 'Neu', 'joined_on': date(2026, 10, 1)},
        members.starting_ranks(set())[0],
        members.DELIVERY_NONE,
        invited_by=inviter,
        permissions=set(),
    )
    db.session.commit()
    assert invitation is None and raw_token is None
    assert open_invitations(member) == []
    assert current_rank_entry(member, date(2026, 10, 1)).valid_from == date(2026, 10, 1)


def test_starting_rank_is_spefux_without_rank_change_permission(app):
    from community.core.org.services import rank_by_key

    assert [r.key for r in members.starting_ranks({'members.invite'})] == ['spefux']
    assert [r.key for r in members.starting_ranks({'members.invite', 'members.rank.change'})] == [
        'spefux',
        'fux',
        'bursch',
        'alter_herr',
    ]
    inviter = make_officer('fuxmajor')
    with pytest.raises(members.MemberError, match='Startrang'):
        members.add_member(
            {'first_name': 'Neu'}, rank_by_key('bursch'), 'link', invited_by=inviter, permissions={'members.invite'}
        )


def test_add_member_with_any_rank_for_rank_changers(app):
    from community.core.org.services import rank_by_key

    senior = make_officer('senior')
    member, _, _ = members.add_member(
        {'first_name': 'Alter', 'last_name': 'Herr'},
        rank_by_key('alter_herr'),
        members.DELIVERY_NONE,
        invited_by=senior,
        permissions={'members.invite', 'members.rank.change'},
    )
    assert current_rank(member).key == 'alter_herr'


def test_add_member_email_delivery_needs_an_address(app):
    inviter = make_officer('fuxmajor')
    with pytest.raises(members.MemberError, match='E-Mail-Adresse'):
        members.add_member(
            {'first_name': 'Neu'},
            members.starting_ranks(set())[0],
            'email',
            invited_by=inviter,
            permissions=set(),
        )


def test_add_member_refuses_an_address_of_another_member(app):
    make_user(email='taken@example.org')
    inviter = make_officer('fuxmajor')
    with pytest.raises(members.EmailInUse):
        members.add_member(
            {'first_name': 'Neu', 'email': 'taken@example.org'},
            members.starting_ranks(set())[0],
            'link',
            invited_by=inviter,
            permissions=set(),
        )


def test_email_invitation_is_queued_and_sent_by_the_job_runner(app):
    inviter = make_officer('fuxmajor', name='Fritz Fuxmajor')
    with app.test_request_context():
        member, invitation, raw_token = members.add_member(
            {'first_name': 'Neu', 'email': 'neu@example.org'},
            members.starting_ranks(set())[0],
            'email',
            invited_by=inviter,
            permissions=set(),
        )
        db.session.commit()
    job = db.session.scalar(select(Job).where(Job.kind == 'mail.send'))
    assert job.payload['to'] == 'neu@example.org'
    assert run_due_jobs()['done'] >= 1
    club = app.config['CLUB_NAME']
    mail = outbox(app)[-1]
    assert mail['to'] == 'neu@example.org'
    assert mail['subject'] == f'Einladung zu {club}'
    assert f'Fritz Fuxmajor hat dich zu {club} eingeladen' in mail['body']
    assert f'http://localhost/join/{raw_token}' in mail['body']
    assert invitation.channel == InvitationChannel.EMAIL


def test_invitation_link_uses_base_url(app):
    app.config['BASE_URL'] = 'https://community.example.org'
    invitation, raw_token = make_invitation()
    assert members.join_url(raw_token) == f'https://community.example.org/join/{raw_token}'


def test_invite_existing_member_with_other_members_address_is_refused(app):
    make_member('Andere Person', email='other@example.org')
    member = make_member('Neu Ling')
    with pytest.raises(members.EmailInUse):
        members.invite(member, 'link', invited_by=None, permissions=set(), email='other@example.org')


def test_invite_existing_member_with_login_is_refused(app):
    user = make_user()
    with pytest.raises(members.MemberError, match='schon einen Login'):
        members.invite(user.member, 'link', invited_by=None, permissions=permissions_for(user.member))


# --- Nobody gets a usable link to rights they lack (M1 review) -------------------

BEYOND_INVITER = 'weder selbst hast noch vergeben darfst'


def login_less_officer(office_key, valid_from=None, email='real@example.org'):
    """A Bursch without login who holds an Amt from `valid_from` (today by default)."""
    from community.core.org.services import assign_office, office_by_key

    member = make_member('Real Officer', email=email)
    set_rank(member, 'bursch')
    assign_office(office_by_key(office_key), member, valid_from or today_local())
    db.session.commit()
    return member


@pytest.mark.parametrize('starts_in_days', [0, 30])
@pytest.mark.parametrize('channel', ['link', 'qr'])
def test_no_link_for_a_member_with_rights_the_inviter_lacks(app, channel, starts_in_days):
    """The Fuxmajor (no members.rank.change, no offices.assign) must not get a link that logs
    him in as the Senior, also not while the Senior's term has not started yet."""
    fm = make_officer('fuxmajor')
    senior = login_less_officer('senior', today_local() + timedelta(days=starts_in_days))
    with pytest.raises(members.MemberError, match=BEYOND_INVITER):
        members.invite(senior, channel, invited_by=fm, permissions=permissions_for(fm.member))
    assert db.session.scalar(select(Invitation)) is None


@pytest.mark.parametrize('starts_in_days', [0, 10])
def test_no_email_to_an_address_of_the_inviters_choice(app, starts_in_days):
    consenior = make_officer('consenior')
    senior = login_less_officer('senior', today_local() + timedelta(days=starts_in_days))
    earlier, earlier_token = make_invitation(senior, channel='email', email='real@example.org')
    with pytest.raises(members.MemberError, match='nur per E-Mail an die hinterlegte Adresse'):
        members.invite(
            senior,
            'email',
            invited_by=consenior,
            permissions=permissions_for(consenior.member),
            email='con@example.org',
        )
    db.session.rollback()
    assert db.session.scalar(select(Job).where(Job.kind == 'mail.send')) is None
    # The real invitee's link was not revoked.
    assert find_valid_invitation(earlier_token).id == earlier.id


def test_email_to_the_stored_address_is_allowed(app):
    fm = make_officer('fuxmajor')
    senior = login_less_officer('senior', email='real@example.org')
    with app.test_request_context():
        invitation, _ = members.invite(
            senior, 'email', invited_by=fm, permissions=permissions_for(fm.member), email=' Real@Example.org '
        )
        db.session.commit()
    assert invitation.email == 'real@example.org'
    (job,) = db.session.scalars(select(Job).where(Job.kind == 'mail.send')).all()
    assert job.payload['to'] == 'real@example.org'


def test_without_stored_address_only_inviters_with_these_rights_invite(app):
    fm = make_officer('fuxmajor')
    senior = login_less_officer('senior', email=None)
    with pytest.raises(members.MemberError, match='keine E-Mail-Adresse hinterlegt'):
        members.invite(senior, 'email', invited_by=fm, permissions=permissions_for(fm.member), email='fm@example.org')
    invitation, _ = members.invite(senior, 'link', invited_by=None, permissions=permissions_for(senior))
    assert invitation.channel == 'link'


def test_rights_from_a_later_rank_or_role_count_too(app):
    from community.core.authz.models import SYSADMIN
    from community.core.authz.service import grant_member_role

    admin = make_admin()
    member = make_member('Spe Fux')
    set_rank(member, 'spefux')
    set_rank(member, 'bursch', today_local() + timedelta(days=7))
    with pytest.raises(members.MemberError, match=BEYOND_INVITER):
        members.invite(member, 'link', invited_by=admin, permissions=permissions_for(admin.member))

    fm = make_officer('fuxmajor')
    other = make_member('Bald Admin')
    set_rank(other, 'bursch')
    grant_member_role(other, SYSADMIN, valid_from=today_local() + timedelta(days=7))
    db.session.commit()
    with pytest.raises(members.MemberError, match=BEYOND_INVITER):
        members.invite(other, 'link', invited_by=fm, permissions=permissions_for(fm.member))


def test_admin_gets_no_link_for_a_login_less_kassenwart(app):
    admin = make_admin()
    kassenwart = login_less_officer('kassenwart')
    for channel in ('link', 'qr'):
        with pytest.raises(members.MemberError, match=BEYOND_INVITER):
            members.invite(kassenwart, channel, invited_by=admin, permissions=permissions_for(admin.member))


@pytest.mark.parametrize('rank', ['spefux', 'fux', 'bursch', 'alter_herr'])
def test_fuxmajor_still_sends_links_to_members_without_amt(app, rank):
    """Go-live: the Fuxmajor (a Bursch) invites imported members of every rank by link."""
    fm = make_officer('fuxmajor')
    member = make_member('Ohne Login')
    set_rank(member, rank)
    invitation, _ = members.invite(member, 'link', invited_by=fm, permissions=permissions_for(fm.member))
    assert invitation.channel == 'link'


def test_admin_still_sends_links_to_new_spefuxe(app):
    """The technical admin has no rank, but may start new members as Spefux and invite them."""
    admin = make_admin()
    perms = permissions_for(admin.member)
    member, invitation, _ = members.add_member(
        {'first_name': 'Neu'}, members.starting_ranks(perms)[0], 'link', invited_by=admin, permissions=perms
    )
    assert invitation.channel == 'link'
    db.session.commit()
    again, _ = members.invite(member, 'qr', invited_by=admin, permissions=perms)
    assert again.channel == 'qr'


# --- Routes: Neues Mitglied, invite, result page, revoke -------------------------


def test_invite_routes_need_login_and_permission(client, login):
    member = make_member('Neu Ling')
    invitation, _ = make_invitation(member)
    urls = [
        ('get', '/members/new'),
        ('post', '/members/new'),
        ('get', f'/m/{member.public_id}/invite'),
        ('post', f'/m/{member.public_id}/invite'),
        ('get', f'/members/invited/{invitation.id}'),
        ('post', f'/invitations/{invitation.id}/revoke'),
    ]
    for method, url in urls:
        response = getattr(client, method)(url)
        assert response.status_code == 302 and '/login' in response.location, url
    make_user(email='bursch@example.org')  # a plain Bursch may not invite
    login('bursch@example.org')
    for method, url in urls:
        assert getattr(client, method)(url).status_code == 403, url
    assert reload(invitation).state() == InvitationState.OPEN


def test_fuxmajor_may_only_start_new_members_as_spefux(client, login):
    fm = make_officer('fuxmajor')
    login(fm)
    page = client.get('/members/new').get_data(as_text=True)
    assert 'Spefux' in page
    assert 'Bursch</option>' not in page
    response = client.post('/members/new', data=new_member_form(rank_id=rank_id('bursch')))
    assert response.status_code == 200
    assert db.session.scalar(select(Member).where(Member.first_name == 'Neu')) is None


def test_senior_may_choose_any_starting_rank(client, login):
    senior = make_officer('senior')
    login(senior)
    page = client.get('/members/new').get_data(as_text=True)
    assert 'Bursch</option>' in page and 'Alter Herr</option>' in page
    response = client.post('/members/new', data=new_member_form(rank_id=rank_id('fux'), delivery='none'))
    assert response.status_code == 302
    member = db.session.scalar(select(Member).where(Member.first_name == 'Neu'))
    assert response.location.endswith(f'/m/{member.public_id}')
    assert current_rank(member).key == 'fux'
    assert open_invitations(member) == []


def test_new_member_with_link_shows_the_link_after_redirect(client, login):
    fm = make_officer('fuxmajor')
    login(fm)
    response = client.post('/members/new', data=new_member_form(email='neu@example.org'))
    assert response.status_code == 302
    invitation = db.session.scalar(select(Invitation))
    assert response.location.endswith(f'/members/invited/{invitation.id}')

    page = client.get(response.location)
    assert page.headers['Cache-Control'] == 'no-store'
    assert page.headers['Referrer-Policy'] == 'no-referrer'
    token = LINK_RE.search(page.get_data(as_text=True)).group(1)
    assert find_valid_invitation(token).id == invitation.id
    assert 'class="segno"' not in page.get_data(as_text=True)
    # A reload shows it again and creates nothing new.
    assert token in client.get(response.location).get_data(as_text=True)
    assert len(db.session.scalars(select(Invitation)).all()) == 1


def test_link_is_shown_only_to_the_inviter_who_created_it(app, client, login):
    fm = make_officer('fuxmajor')
    login(fm)
    response = client.post('/members/new', data=new_member_form())
    location = response.location
    client.post('/logout')
    other = make_officer('senior')
    login(other)
    page = client.get(location).get_data(as_text=True)
    assert LINK_RE.search(page) is None
    assert 'nur direkt nach dem Erstellen' in page


def test_new_member_with_qr_shows_an_svg(client, login):
    login(make_officer('fuxmajor'))
    response = client.post('/members/new', data=new_member_form(delivery='qr'))
    page = client.get(response.location).get_data(as_text=True)
    assert '<svg width=' in page and 'class="segno"' in page
    assert 'Einladung für Neu Ling' in page
    invitation = db.session.scalar(select(Invitation))
    assert invitation.channel == 'qr'
    assert invitation.expires_at - invitation.created_at == timedelta(hours=24)


def test_new_member_with_email_queues_the_mail_and_hides_the_link(app, client, login):
    login(make_officer('fuxmajor'))
    response = client.post('/members/new', data=new_member_form(delivery='email', email='Neu@Example.org'))
    assert response.status_code == 302
    page = client.get(response.location).get_data(as_text=True)
    assert LINK_RE.search(page) is None
    assert 'neu@example.org' in page
    assert db.session.scalar(select(Job).where(Job.kind == 'mail.send')) is not None
    run_due_jobs()
    mail = next(m for m in outbox(app) if m['to'] == 'neu@example.org')
    token = LINK_RE.search(mail['body']).group(1)
    assert find_valid_invitation(token) is not None
    # Once sent, the link is nowhere in the database, only its hash (finding 9).
    assert all(token not in job.payload_json for job in db.session.scalars(select(Job)))


def test_new_member_email_delivery_without_address_shows_form_error(client, login):
    login(make_officer('fuxmajor'))
    response = client.post('/members/new', data=new_member_form(delivery='email'))
    assert response.status_code == 200
    assert members.EMAIL_NEEDED in response.get_data(as_text=True)
    assert db.session.scalar(select(Invitation)) is None


def test_new_member_with_taken_email_shows_form_error(client, login):
    make_user(email='taken@example.org')
    login(make_officer('fuxmajor'))
    response = client.post('/members/new', data=new_member_form(email='taken@example.org'))
    assert response.status_code == 200
    assert members.EMAIL_IN_USE in response.get_data(as_text=True)


def test_directory_has_new_member_button_for_inviters(client, login):
    login(make_user(email='bursch@example.org'))
    assert '/members/new' not in client.get('/members').get_data(as_text=True)
    client.post('/logout')
    login(make_officer('fuxmajor'))
    assert '/members/new' in client.get('/members').get_data(as_text=True)


def test_member_page_offers_invite_only_without_login(client, login):
    with_login = make_user(email='hat@example.org', name='Hat Login')
    without = make_member('Ohne Login')
    invitation, _ = make_invitation(without)
    login(make_officer('fuxmajor'))
    page = client.get(f'/m/{without.public_id}').get_data(as_text=True)
    assert f'/m/{without.public_id}/invite' in page
    assert f'/invitations/{invitation.id}/revoke' in page
    page = client.get(f'/m/{with_login.member.public_id}').get_data(as_text=True)
    assert '/invite' not in page


def test_invite_existing_member(client, login):
    member = make_member('Neu Ling', email='neu@example.org')
    old, old_token = make_invitation(member)
    login(make_officer('fuxmajor'))
    page = client.get(f'/m/{member.public_id}/invite').get_data(as_text=True)
    assert 'value="neu@example.org"' in page
    response = client.post(f'/m/{member.public_id}/invite', data={'delivery': 'link', 'email': 'neu@example.org'})
    assert response.status_code == 302
    token = LINK_RE.search(client.get(response.location).get_data(as_text=True)).group(1)
    assert find_valid_invitation(token).email == 'neu@example.org'
    assert find_valid_invitation(old_token) is None


def test_invite_page_for_a_member_with_more_rights_offers_only_the_stored_address(client, login):
    senior = login_less_officer('senior', email='real@example.org')
    login(make_officer('fuxmajor'))
    url = f'/m/{senior.public_id}/invite'
    page = client.get(url).get_data(as_text=True)
    assert BEYOND_INVITER in page
    assert 'value="email"' in page and 'value="link"' not in page and 'value="qr"' not in page
    for data in (
        {'delivery': 'link', 'email': ''},
        {'delivery': 'qr', 'email': ''},
        {'delivery': 'email', 'email': 'fm-alt@example.org'},
    ):
        assert client.post(url, data=data).status_code == 200, data
    assert db.session.scalar(select(Invitation)) is None
    response = client.post(url, data={'delivery': 'email', 'email': 'real@example.org'})
    assert response.status_code == 302
    assert LINK_RE.search(client.get(response.location).get_data(as_text=True)) is None
    assert db.session.scalar(select(Invitation)).email == 'real@example.org'


def test_invite_page_without_stored_address_explains_who_may_invite(client, login):
    senior = login_less_officer('senior', email=None)
    login(make_officer('fuxmajor'))
    page = client.get(f'/m/{senior.public_id}/invite').get_data(as_text=True)
    assert 'keine E-Mail-Adresse hinterlegt' in page
    assert 'name="delivery"' not in page


def session_data(app, client):
    """The session cookie as anyone with access to the browser can decode it (signed, not encrypted)."""
    cookie = client.get_cookie(app.config['SESSION_COOKIE_NAME'])
    if cookie is None:
        return {}
    return app.session_interface.get_signing_serializer(app).loads(cookie.value)


@pytest.mark.parametrize('how', ['logout', 'logout_everywhere', 'ended_elsewhere'])
def test_invitation_link_leaves_the_session_with_the_login(app, client, login, how):
    """M1 review: the raw token must not stay in the cookie for the next person at the browser."""
    fm = make_officer('fuxmajor')
    login(fm)
    response = client.post('/members/new', data=new_member_form())
    raw_token = LINK_RE.search(client.get(response.location).get_data(as_text=True)).group(1)
    assert raw_token in str(session_data(app, client))  # while logged in, a reload shows it again
    if how == 'logout':
        client.post('/logout')
    elif how == 'logout_everywhere':
        client.post('/profile/logout-everywhere')
    else:  # e.g. "Überall abmelden" on another device; this browser comes back later
        end_all_sessions(reload(fm))
        db.session.commit()
        client.get('/login')
    assert raw_token not in str(session_data(app, client))
    login(make_user(email='next@example.org', name='Next Person'))
    assert raw_token not in str(session_data(app, client))
    assert LINK_RE.search(client.get(response.location).get_data(as_text=True)) is None


def test_member_with_login_is_not_invited(client, login):
    user = make_user(email='hat@example.org', name='Hat Login')
    login(make_officer('fuxmajor'))
    page = client.get(f'/m/{user.member.public_id}/invite').get_data(as_text=True)
    assert 'hat schon einen Login' in page
    response = client.post(f'/m/{user.member.public_id}/invite', data={'delivery': 'link'})
    assert response.status_code == 200
    assert db.session.scalar(select(Invitation)) is None


def test_revoke_route(client, login):
    member = make_member('Neu Ling')
    invitation, raw_token = make_invitation(member)
    login(make_officer('fuxmajor'))
    response = client.post(f'/invitations/{invitation.id}/revoke')
    assert response.status_code == 302
    assert response.location.endswith(f'/m/{member.public_id}')
    db.session.expire_all()
    assert find_valid_invitation(raw_token) is None
    assert client.post('/invitations/9999/revoke').status_code == 404
    # Revoking twice only shows a message.
    client.post(f'/invitations/{invitation.id}/revoke')
    assert len(audit_entries('identity.invitation.revoke')) == 1


def test_seed_inviters(client, login):
    """§6.5: Senior, Consenior, Fuxmajor and the technical admin invite; plain ranks do not."""
    for office in ('senior', 'consenior', 'fuxmajor'):
        login(make_officer(office))
        assert client.get('/members/new').status_code == 200, office
        client.post('/logout')
    login(make_admin())
    assert client.get('/members/new').status_code == 200


# --- Joining ------------------------------------------------------------------------


def test_join_page_shows_club_inviter_and_member(app, client):
    inviter = make_officer('fuxmajor', name='Fritz Fuxmajor')
    member = make_member('Neu Ling', biername='Küken', email='neu@example.org')
    _, raw_token = make_invitation(member, created_by=inviter)
    response = client.get(f'/join/{raw_token}')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    page = response.get_data(as_text=True)
    assert f'Willkommen bei {app.config["CLUB_NAME"]}' in page
    assert 'Fritz Fuxmajor hat dich eingeladen' in page
    assert 'Neu Ling' in page and 'Küken' in page
    assert 'value="neu@example.org"' in page


def test_join_prefills_the_invitation_email(client):
    member = make_member('Neu Ling', email='kontakt@example.org')
    _, raw_token = make_invitation(member, email='einladung@example.org')
    assert 'value="einladung@example.org"' in client.get(f'/join/{raw_token}').get_data(as_text=True)


def test_join_creates_login_logs_in_and_notifies(client):
    fm = make_officer('fuxmajor')
    member = make_member('Neu Ling')
    invitation, raw_token = make_invitation(member, created_by=fm)
    response = client.post(f'/join/{raw_token}', data=join_form(login_email=' Neu@Example.org '))
    assert response.status_code == 302
    assert response.location.endswith('/profile')

    user = db.session.scalar(select(User).where(User.member_id == member.id))
    assert user.login_email == 'neu@example.org'
    assert user.check_password(NEW_PASSWORD)
    assert user.last_login_at is not None
    assert reload(invitation).state() == InvitationState.ACCEPTED
    assert is_logged_in(client)
    welcome = db.session.scalars(
        select(Notification).where(Notification.member_id == member.id, Notification.kind == 'welcome')
    ).all()
    assert len(welcome) == 1 and welcome[0].link == '/profile'
    to_fm = db.session.scalars(
        select(Notification).where(Notification.member_id == fm.member_id, Notification.kind == 'new_member')
    ).all()
    assert len(to_fm) == 1 and to_fm[0].link == f'/m/{member.public_id}'
    assert audit_entries('identity.invitation.accept')[-1].actor_user_id == user.id
    # The link is used up: a second visit gets the "invalid" page.
    client.post('/logout')
    assert client.get(f'/join/{raw_token}').status_code == 404


def test_join_welcome_flash(client):
    _, raw_token = make_invitation()
    response = client.post(f'/join/{raw_token}', data=join_form(), follow_redirects=True)
    assert 'Willkommen, Neu!' in response.get_data(as_text=True)


def test_join_then_login_with_new_password(client):
    _, raw_token = make_invitation()
    client.post(f'/join/{raw_token}', data=join_form())
    client.post('/logout')
    assert not is_logged_in(client)
    client.post('/login', data={'email': 'neu@example.org', 'password': NEW_PASSWORD})
    assert is_logged_in(client)


def test_join_with_used_email_shows_form_error(client):
    make_user(email='taken@example.org')
    invitation, raw_token = make_invitation()
    response = client.post(f'/join/{raw_token}', data=join_form(login_email='Taken@example.org'))
    assert response.status_code == 200
    assert 'gehört schon zu einem anderen Konto' in response.get_data(as_text=True)
    assert reload(invitation).state() == InvitationState.OPEN
    assert reload(invitation).member.user is None
    assert not is_logged_in(client)


def test_join_rejects_short_password_with_message(client):
    _, raw_token = make_invitation()
    response = client.post(f'/join/{raw_token}', data=join_form(password='short', confirm='short'))
    assert 'Mindestens 10 Zeichen' in response.get_data(as_text=True)
    assert db.session.scalar(select(User)) is None


def test_join_rejects_mismatched_passwords(client):
    _, raw_token = make_invitation()
    response = client.post(f'/join/{raw_token}', data=join_form(confirm='etwas ganz anderes'))
    assert 'stimmen nicht überein' in response.get_data(as_text=True)
    assert db.session.scalar(select(User)) is None


@pytest.mark.parametrize('how', ['unknown', 'expired', 'revoked', 'used'])
def test_unusable_links_get_the_same_friendly_page(client, how):
    invitation, raw_token = make_invitation()
    if how == 'unknown':
        raw_token = 'x' * 43
    elif how == 'expired':
        invitation.expires_at = utcnow() - timedelta(minutes=1)
    elif how == 'revoked':
        revoke_invitation(invitation, revoked_by=None)
    else:
        accept_invitation(invitation, 'erster@example.org', NEW_PASSWORD)
    db.session.commit()
    users_before = len(db.session.scalars(select(User)).all())

    response = client.get(f'/join/{raw_token}')
    assert response.status_code == 404
    page = response.get_data(as_text=True)
    assert 'Link ungültig oder abgelaufen – bitte einen neuen anfordern' in page
    for detail in ('abgelaufen am', 'widerrufen', 'angenommen', 'schon benutzt'):
        assert detail not in page
    assert client.post(f'/join/{raw_token}', data=join_form()).status_code == 404
    assert len(db.session.scalars(select(User)).all()) == users_before


def test_join_while_logged_in_is_refused(client, login):
    user = make_user()
    _, raw_token = make_invitation()
    login(user)
    page = client.get(f'/join/{raw_token}').get_data(as_text=True)
    assert 'bitte zuerst abmelden' in page
    client.post(f'/join/{raw_token}', data=join_form())
    assert db.session.scalar(select(User).where(User.login_email == 'neu@example.org')) is None


def test_join_without_fuxmajor_notifies_nobody_else(client):
    member = make_member('Neu Ling')
    _, raw_token = make_invitation(member)
    client.post(f'/join/{raw_token}', data=join_form())
    kinds = set(db.session.scalars(select(Notification.kind)))
    assert kinds == {'welcome'}


def test_join_gives_the_starting_rank_rights(client):
    """The new login has exactly the rights of the rank set when the member was created."""
    member = make_member('Neu Ling')
    set_rank(member, 'spefux')
    _, raw_token = make_invitation(member)
    client.post(f'/join/{raw_token}', data=join_form())
    assert [r.key for r in active_roles(reload(member))] == ['rank.spefux']
    assert client.get('/announcements').status_code == 200  # Spefux: announcements.view
    assert client.get('/members').status_code == 403


# --- Shared invite code is gone ------------------------------------------------------


def test_register_route_is_gone(client):
    assert client.get('/register').status_code == 404
    assert client.post('/register', data={'invite': 'test-invite'}).status_code == 404


def test_login_page_explains_invitations(client):
    page = client.get('/login').get_data(as_text=True)
    assert 'Kein Konto? Du bekommst einen Einladungslink vom Senior/Fuxmajor.' in page
    assert 'Registrieren' not in page


def test_admin_email_does_not_promote(app, client, monkeypatch):
    """K8/N1: joining with the old ADMIN_EMAIL address must not create an admin."""
    monkeypatch.setenv('ADMIN_EMAIL', 'neu@example.org')
    app.config['ADMIN_EMAIL'] = 'neu@example.org'
    member = make_member('Neu Ling')
    _, raw_token = make_invitation(member)
    client.post(f'/join/{raw_token}', data=join_form())
    assert reload(member).user is not None
    assert active_roles(reload(member)) == []


def test_former_members_invitation_pages_follow_the_member_page_rule(client, login):
    """Decision 23: a former member's page is a 404 without members.edit_any / members.exit.record."""
    invitation, _ = make_invitation()
    invitation.member.status = MemberStatus.ENDED
    db.session.commit()
    login(make_officer('fuxmajor'))
    assert client.get(f'/members/invited/{invitation.id}').status_code == 404
    assert client.post(f'/invitations/{invitation.id}/revoke').status_code == 404
