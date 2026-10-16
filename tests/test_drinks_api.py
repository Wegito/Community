"""M6 (first part): the API for the bar tablet (roadmap §7.3) and the device tokens."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from community.core.identity.models import MemberStatus
from community.core.platform.api import ApiClient, client_for_token, create_client
from community.core.time import utcnow
from community.extensions import db
from community.modules.drinks import services as drinks
from community.modules.drinks.models import DrinkConsumption

from .factories import make_member, make_officer

ALL_SCOPES = ['products:read', 'members:read', 'drinks:write']


@pytest.fixture
def setup(app):
    pils = drinks.create_product(
        'Pils', 120, counts_as_beer=True, valid_from=utcnow().date() - timedelta(days=30), actor=None
    )
    cola = drinks.create_product(
        'Cola', 150, counts_as_beer=False, valid_from=utcnow().date() - timedelta(days=30), actor=None
    )
    member = make_member('Max Muster', email='max@example.org')
    client, token = create_client('Theken-Tablet', ALL_SCOPES, created_by=None)
    db.session.commit()
    return {
        'pils': pils,
        'cola': cola,
        'member': member,
        'client': client,
        'headers': {'Authorization': f'Bearer {token}'},
    }


def item(setup, event='e1', quantity=2, **overrides):
    return {
        'client_event_id': event,
        'member_id': setup['member'].public_id,
        'product_id': setup['pils'].id,
        'quantity': quantity,
        'occurred_at': (utcnow() - timedelta(minutes=5)).isoformat(),
        **overrides,
    }


def send(client, setup, *items):
    return client.post('/api/v1/drinks/consumptions', json={'items': list(items)}, headers=setup['headers'])


def rows():
    return db.session.scalar(select(func.count()).select_from(DrinkConsumption))


def test_products_and_members_for_the_tablet(client, setup):
    body = client.get('/api/v1/products', headers=setup['headers']).get_json()
    assert {p['name']: p['price_cents'] for p in body['items']} == {'Pils': 120, 'Cola': 150}
    body = client.get('/api/v1/members', headers=setup['headers']).get_json()
    assert body['items'] == [{'id': setup['member'].public_id, 'name': 'Max Muster', 'biername': None}]
    assert 'max@example.org' not in str(body)  # data minimisation: no emails
    db.session.expire_all()
    assert setup['client'].last_used_at is not None


def test_consumptions_are_stored_once_and_priced_by_the_server(client, setup):
    first = item(setup)
    body = send(client, setup, first, item(setup, 'e2', product_id=setup['cola'].id, quantity=1)).get_json()
    assert [r['status'] for r in body['results']] == ['accepted', 'accepted']
    retry = send(client, setup, first).get_json()  # the tablet sends the same again after a timeout
    assert retry['results'] == [{'client_event_id': 'e1', 'status': 'duplicate'}]
    assert rows() == 2
    row = db.session.scalar(select(DrinkConsumption).where(DrinkConsumption.product_id == setup['pils'].id))
    assert (row.quantity, row.unit_price_cents, row.source, row.api_client_id) == (2, 120, 'api', setup['client'].id)


def test_each_bad_item_is_rejected_with_a_reason(client, setup):
    pils = setup['pils']
    send(client, setup, item(setup))
    gone = make_member('Ex Mitglied')
    gone.status = MemberStatus.ENDED
    drinks.update_product(setup['cola'], name='Cola', counts_as_beer=False, volume_ml=None, archived=True, actor=None)
    db.session.commit()
    cases = [
        (item(setup, quantity=3), 'client_event_id_reused'),
        (item(setup, 'x1', member_id='nobody'), 'unknown_member'),
        (item(setup, 'x2', member_id=gone.public_id), 'inactive_member'),
        (item(setup, 'x3', product_id=9999), 'unknown_product'),
        (item(setup, 'x4', product_id=setup['cola'].id), 'inactive_product'),
        (item(setup, 'x5', quantity=0), 'invalid_quantity'),
        (item(setup, 'x6', quantity='2'), 'invalid_quantity'),
        (item(setup, 'x7', occurred_at=(utcnow() + timedelta(hours=1)).isoformat()), 'occurred_at_in_future'),
        (item(setup, 'x8', occurred_at='2026-10-11T21:14:00'), 'invalid_occurred_at'),
        (item(setup, 'x9', quantity=-2, corrects_client_event_id='nope'), 'invalid_correction'),
        ({'member_id': 'x'}, 'invalid_client_event_id'),
        ('not an object', 'invalid_item'),
    ]
    body = send(client, setup, *(case for case, _ in cases), item(setup, 'ok', product_id=pils.id)).get_json()
    reasons = [(r['status'], r.get('reason')) for r in body['results']]
    assert reasons == [('rejected', reason) for _, reason in cases] + [('accepted', None)]
    assert rows() == 2


def test_a_correction_from_the_tablet(client, setup):
    send(client, setup, item(setup))
    fix = item(setup, 'e1-undo', quantity=-2, corrects_client_event_id='e1')
    assert send(client, setup, fix).get_json()['results'][0]['status'] == 'accepted'
    assert send(client, setup, fix).get_json()['results'][0]['status'] == 'duplicate'
    again = item(setup, 'e1-undo-2', quantity=-2, corrects_client_event_id='e1')
    assert send(client, setup, again).get_json()['results'][0]['reason'] == 'already_corrected'
    assert drinks.deckel(setup['member'], *drinks.month_range()).beers == 0


def test_batch_and_body_errors(client, setup):
    response = client.post('/api/v1/drinks/consumptions', data='nonsense', headers=setup['headers'])
    assert response.status_code == 422 and response.mimetype == 'application/problem+json'
    assert send(client, setup).status_code == 422  # empty list
    too_many = [item(setup, f'e{i}') for i in range(drinks.API_BATCH_MAX + 1)]
    assert send(client, setup, *too_many).status_code == 422
    assert rows() == 0


def test_tokens_are_stored_hashed_and_can_be_revoked(client, login, setup):
    token = setup['headers']['Authorization'].split()[1]
    stored = db.session.scalar(select(ApiClient))
    assert token not in (stored.token_hash, stored.token_hint) and stored.token_hint.endswith('…')
    assert client_for_token(token).id == stored.id and client_for_token('cmt_wrong') is None

    login(make_officer('bierwart'))
    page = client.get('/getraenke/geraete').get_data(as_text=True)
    assert 'Theken-Tablet' in page and token not in page
    client.post(f'/getraenke/geraete/{stored.id}/sperren')
    response = client.get('/api/v1/products', headers=setup['headers'])
    assert response.status_code == 401


def test_bierwart_creates_a_device_and_sees_the_token_once(client, login, app):
    login(make_officer('bierwart'))
    page = client.post('/getraenke/geraete', data={'name': 'Kühlschrank', 'scopes': ['drinks:write']}).get_data(
        as_text=True
    )
    token = page.split('font-mono text-sm text-slate-900">')[1].split('<')[0]
    assert token.startswith('cmt_') and 'nur jetzt sichtbar' in page
    assert token not in client.get('/getraenke/geraete').get_data(as_text=True)
    api = app.test_client()
    assert api.get('/api/v1/products', headers={'Authorization': f'Bearer {token}'}).status_code == 403  # no scope
    page = client.post('/getraenke/geraete', data={'name': 'Leer', 'scopes': []}).get_data(as_text=True)
    assert 'mindestens eine' in page


def test_the_contract_lists_exactly_the_api_routes(app):
    from pathlib import Path

    import yaml

    contract = yaml.safe_load((Path(__file__).parent.parent / 'docs/api/openapi.yaml').read_text())
    routes = {
        (rule.rule.removeprefix('/api/v1'), method.lower())
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith('/api/v1/')
        for method in rule.methods - {'HEAD', 'OPTIONS'}
    }
    documented = {(path, method) for path, methods in contract['paths'].items() for method in methods}
    assert routes == documented
