"""Every route: anonymous visitors are sent to login, members and admins get what they may see.

ROUTES must list every route of the app; test_every_route_is_listed fails when a new one is added
without deciding who may use it.
"""

import re
from datetime import timedelta

import pytest

from community.core.time import utcnow
from community.extensions import db
from community.modules.bookings.models import Resource
from community.modules.bookings.services import create_booking
from community.modules.tasks.models import Chore

from .factories import make_admin, make_user

# rule -> (method, who may use it)
#   public: anyone; member: any logged-in user; admin: admins only;
#   owner: the member the object belongs to (or an admin); post-only routes are checked anonymously only here.
ROUTES = {
    '/login': ('GET', 'public'),
    '/register': ('GET', 'public'),
    '/logout': ('POST', 'member'),
    '/': ('GET', 'member'),
    '/announcements': ('GET', 'member'),
    '/announcements/new': ('GET', 'admin'),
    '/chores': ('GET', 'member'),
    '/chores/new': ('GET', 'admin'),
    '/chores/edit/<int:cid>': ('GET', 'admin'),
    '/chores/<int:cid>/toggle': ('POST', 'owner'),
    '/bookings': ('GET', 'member'),
    '/bookings/<int:booking_id>/cancel': ('POST', 'owner'),
    '/bookings/resources': ('GET', 'admin'),
    '/bookings/resources/<int:resource_id>/toggle': ('POST', 'admin'),
    '/admin/audit': ('GET', 'admin'),
}


@pytest.fixture
def data(app):
    admin = make_admin()
    member = make_user()
    chore = Chore(title='Küche', assigned_to_id=member.id)
    resource = Resource(name='Saal')
    db.session.add_all([chore, resource])
    db.session.commit()
    start = (utcnow() + timedelta(days=1)).replace(microsecond=0)
    booking = create_booking(resource.id, member.id, start, start + timedelta(hours=1))
    return {'admin': admin, 'member': member, 'cid': chore.id, 'booking_id': booking.id, 'resource_id': resource.id}


def url_for_rule(rule, data):
    return re.sub(r'<int:(\w+)>', lambda m: str(data[m.group(1)]), rule)


def routes(*access):
    return [rule for rule, (_, who) in ROUTES.items() if who in access]


def test_every_route_is_listed(app):
    rules = {rule.rule for rule in app.url_map.iter_rules() if rule.endpoint != 'static'}
    assert rules == set(ROUTES)


@pytest.mark.parametrize('rule', routes('public'))
def test_public_pages_render(client, rule):
    assert client.get(rule).status_code == 200


@pytest.mark.parametrize('rule', routes('member', 'admin', 'owner'))
def test_anonymous_is_sent_to_login(client, data, rule):
    method, _ = ROUTES[rule]
    response = client.open(url_for_rule(rule, data), method=method)
    assert response.status_code == 302
    assert '/login' in response.location


@pytest.mark.parametrize('rule', [r for r in routes('member', 'admin') if ROUTES[r][0] == 'GET'])
def test_admin_gets_every_page(client, login, data, rule):
    login(data['admin'])
    assert client.get(url_for_rule(rule, data)).status_code == 200


@pytest.mark.parametrize('rule', [r for r in routes('member') if ROUTES[r][0] == 'GET'])
def test_member_gets_member_pages(client, login, data, rule):
    login(data['member'])
    assert client.get(url_for_rule(rule, data)).status_code == 200


@pytest.mark.parametrize('rule', routes('admin'))
def test_member_is_refused_admin_routes(client, login, data, rule):
    method, _ = ROUTES[rule]
    login(data['member'])
    response = client.open(url_for_rule(rule, data), method=method)
    # admin_required redirects to the start page; object checks answer 403.
    assert response.status_code in (302, 403)
    if response.status_code == 302:
        assert '/login' not in response.location
