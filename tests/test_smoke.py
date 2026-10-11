"""Every route declares who may use it (@public, @require_login, @require_perm) and behaves accordingly.

The checks are generated from the routes themselves, so a new route is tested automatically,
and test_every_route_declares_access fails for a route without a declaration.
"""

import re
from datetime import date, timedelta

import pytest

from community import create_app
from community.config import TestingConfig
from community.core.org.services import add_to_group, assign_office, create_group, create_semester, office_by_key
from community.core.platform.jobs import enqueue
from community.core.platform.notify import notify
from community.core.time import utcnow
from community.extensions import db
from community.modules.bookings.models import Resource
from community.modules.bookings.services import create_booking
from community.modules.tasks.models import Chore

from .factories import give_permissions, make_user


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
    }


def url(rule, data):
    return re.sub(r'<(?:int:)?(\w+)>', lambda m: str(data[m.group(1)]), rule)


def test_every_route_declares_access():
    missing = [rule for rule, _, access in RULES if access is None]
    assert missing == [], f'Routes without @public, @require_login or @require_perm: {missing}'


@pytest.mark.parametrize(('rule', 'method', 'access'), [r for r in RULES if r[2][0] == 'public'], ids=ids)
def test_public_routes_render(client, rule, method, access):
    assert client.open(rule, method=method).status_code == 200


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
