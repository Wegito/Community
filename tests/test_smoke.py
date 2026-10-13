"""Every route declares who may use it (@public, @require_login, @require_perm) and behaves accordingly.

The checks are generated from the routes themselves, so a new route is tested automatically,
and test_every_route_declares_access fails for a route without a declaration.
"""

import html
import re
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from community import create_app
from community.config import TestingConfig
from community.core.identity.services import create_invitation
from community.core.org.services import add_to_group, assign_office, create_group, create_semester, office_by_key
from community.core.platform.jobs import enqueue
from community.core.platform.notify import notify
from community.core.platform.qr import tokens_for
from community.core.time import utcnow
from community.extensions import db
from community.modules.auth.services import email_change_token, password_reset_token
from community.modules.bookings.models import Resource
from community.modules.bookings.services import create_booking
from community.modules.finance.charges import save_recurring
from community.modules.finance.models import FinCategory
from community.modules.finance.services import Line, account_by_key, member_account, post_journal
from community.modules.laundry.models import LaundryMachine
from community.modules.laundry.services import record_use
from community.modules.tasks.models import Chore

from .factories import give_permissions, make_admin, make_member, make_officer, make_user


def _rules():
    app = create_app(TestingConfig)
    result = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == 'static':
            continue
        view = app.view_functions[rule.endpoint]
        method = 'GET' if 'GET' in rule.methods else 'POST'
        result.append((rule.rule, method, getattr(view, '_access', None)))
    return sorted(result)


RULES = _rules()


def ids(value):
    return value[0] if isinstance(value, tuple) else None


@pytest.fixture
def data(app):
    member = make_user(email='owner@example.org', name='Owner Person')
    chore = Chore(title='Küche', assigned_to_id=member.id)
    resource = Resource(name='Saal')
    db.session.add_all([chore, resource])
    db.session.commit()
    start = (utcnow() + timedelta(days=1)).replace(microsecond=0)
    booking = create_booking(resource.id, member.id, start, start + timedelta(hours=1))
    semester = create_semester('WS26/27', 'WS', date(2026, 10, 1), date(2027, 3, 31))
    assignment = assign_office(office_by_key('kassenwart'), member.member, date(2026, 10, 1))
    group = create_group('Küche A')
    membership = add_to_group(group, member.member, date(2026, 10, 1))
    job = enqueue('test.smoke', {})
    notification = notify(member.member, 'rank_changed', 'Test')
    invitation, _ = create_invitation(make_member('Neu Ling'), 'link', created_by=member)
    laundry_use, _ = record_use(member.member, washes=1, dried=False, submission_id='smoke', recorded_by=member)
    journal, _ = post_journal(
        'payment',
        'Smoke',
        date(2026, 10, 2),
        [Line(account_by_key('giro'), member_account(member.member), 100)],
        posted_by=None,
    )
    recurring = save_recurring(
        None,
        title='Smoke',
        amount_cents=750,
        target=account_by_key('beitraege'),
        category=None,
        rank_keys=['bursch'],
        group=None,
        actor=None,
    )
    db.session.commit()
    # URL parameters by name, filled in for rules like /chores/edit/<int:cid>.
    return {
        'cid': chore.id,
        'booking_id': booking.id,
        'resource_id': resource.id,
        'public_id': member.member.public_id,
        'semester_id': semester.id,
        'key': 'kassenwart',
        'assignment_id': assignment.id,
        'group_id': group.id,
        'membership_id': membership.id,
        'job_id': job.id,
        'notification_id': notification.id,
        'invitation_id': invitation.id,
        'token': 'not-a-valid-token',
        'use_id': laundry_use.id,
        'machine_id': db.session.scalars(select(LaundryMachine)).first().id,
        'qr_id': tokens_for('laundry')[0].id,
        'journal_id': journal.id,
        'account_id': account_by_key('bar').id,
        'category_id': db.session.scalars(select(FinCategory)).first().id,
        'recurring_id': recurring.id,
    }


def url(rule, data):
    return re.sub(r'<(?:int:)?(\w+)>', lambda m: str(data[m.group(1)]), rule)


def test_every_route_declares_access():
    missing = [rule for rule, _, access in RULES if access is None]
    assert missing == [], f'Routes without @public, @require_login or @require_perm: {missing}'


@pytest.mark.parametrize(('rule', 'method', 'access'), [r for r in RULES if r[2][0] == 'public'], ids=ids)
def test_public_routes_render(client, rule, method, access):
    response = client.open(rule, method=method)
    if '<' in rule:
        # A public rule with a parameter (/join/<token>) gets the literal placeholder: an unknown
        # token is a friendly "not found", never an error.
        assert response.status_code in (404, 410)
    else:
        assert response.status_code == 200


@pytest.fixture
def valid_tokens(app):
    """A usable token for each public rule with a <token>, so those pages are rendered for real once."""
    owner = make_user(email='token@example.org', name='Token Owner')
    _, invitation_token = create_invitation(make_member('Neu Ling'), 'link', created_by=owner)
    db.session.commit()
    return {
        '/join/<token>': invitation_token,
        '/password/reset/<token>': password_reset_token(owner),
        '/settings/email/confirm/<token>': email_change_token(owner, 'new@example.org'),
        '/q/<token>': tokens_for('laundry')[0].token,
    }


@pytest.mark.parametrize(
    ('rule', 'method', 'access'), [r for r in RULES if r[2][0] == 'public' and '<' in r[0]], ids=ids
)
def test_public_token_routes_render_with_a_valid_token(client, valid_tokens, rule, method, access):
    assert rule in valid_tokens, f'add a valid token for {rule} to the valid_tokens fixture'
    response = client.get(url(rule, {'token': valid_tokens[rule]}))
    # A printed QR code only forwards to the page it stands for.
    assert response.status_code == (302 if rule == '/q/<token>' else 200)
    # The URL holds the token: keep it out of caches and Referer headers.
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Referrer-Policy'] == 'no-referrer'


@pytest.mark.parametrize(('rule', 'method', 'access'), [r for r in RULES if r[2][0] != 'public'], ids=ids)
def test_anonymous_is_sent_to_login(client, data, rule, method, access):
    response = client.open(url(rule, data), method=method)
    assert response.status_code == 302
    assert '/login' in response.location


@pytest.mark.parametrize(('rule', 'method', 'access'), [r for r in RULES if r[2][0] == 'perm'], ids=ids)
def test_permission_is_required(client, login, data, rule, method, access):
    make_user(email='nobody@example.org', name='No Rights', perms=[])
    login('nobody@example.org')
    assert client.open(url(rule, data), method=method).status_code == 403


@pytest.mark.parametrize(
    ('rule', 'method', 'access'), [r for r in RULES if r[2][0] == 'perm' and r[1] == 'GET'], ids=ids
)
def test_permission_is_enough(client, login, data, rule, method, access):
    user = make_user(email='holder@example.org', name='Perm Holder', perms=[])
    give_permissions(user.member, access[1])
    login(user)
    assert client.get(url(rule, data)).status_code == 200


@pytest.mark.parametrize(
    ('rule', 'method', 'access'), [r for r in RULES if r[2][0] == 'login' and r[1] == 'GET'], ids=ids
)
def test_login_routes_work_without_permissions(client, login, data, rule, method, access):
    make_user(email='nobody@example.org', name='No Rights', perms=[])
    login('nobody@example.org')
    assert client.get(url(rule, data)).status_code == 200


@pytest.mark.parametrize('who', ['admin', 'senior', 'no rights'])
def test_pages_link_only_to_pages_the_user_may_open(client, login, data, who):
    """Every <a href> on every page a user opens leads to a page they may open (never 403)."""
    user = {
        'admin': make_admin,
        'senior': lambda: make_officer('senior', email='senior@example.org'),
        'no rights': lambda: make_user(email='nobody@example.org', name='No Rights', perms=[]),
    }[who]()
    login(user)
    status, forbidden = {}, []
    for rule, method, access in RULES:
        if method != 'GET' or access[0] == 'public':
            continue
        page = client.get(url(rule, data))
        if page.status_code != 200:
            continue
        for href in re.findall(r'<a [^>]*href="(/[^"#]*)', page.get_data(as_text=True)):
            href = html.unescape(href)
            if href not in status and not href.startswith('/static/'):
                status[href] = client.get(href).status_code
            if status.get(href) == 403:
                forbidden.append((rule, href))
    assert forbidden == []
