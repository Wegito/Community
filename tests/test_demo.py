"""M8-4 (part): `flask seed demo` fills an empty development database with a made-up club."""

from sqlalchemy import func, select

from community.core.demo import DEMO_PASSWORD
from community.core.identity.models import Member
from community.extensions import db
from community.modules.finance.models import FinJournal, FinRequest


def count(model):
    return db.session.scalar(select(func.count()).select_from(model))


def test_demo_club_can_be_used_right_away(app, client):
    result = app.test_cli_runner().invoke(args=['seed', 'demo'])
    assert result.exit_code == 0, result.output
    assert DEMO_PASSWORD in result.output and 'pruefer@demo.de' in result.output
    assert count(Member) == 11 and count(FinJournal) >= 8 and count(FinRequest) == 1

    client.post('/login', data={'email': 'pruefer@demo.de', 'password': DEMO_PASSWORD})
    page = client.get('/kasse/').get_data(as_text=True)
    assert '1 Buchung wartet auf Gegenzeichnung' in page

    again = app.test_cli_runner().invoke(args=['seed', 'demo'])
    assert again.exit_code != 0 and 'already has members' in again.output
    assert count(Member) == 11


def test_demo_data_never_in_production(app):
    app.config['ENV_NAME'] = 'production'
    result = app.test_cli_runner().invoke(args=['seed', 'demo'])
    assert result.exit_code != 0 and 'only in development' in result.output
    assert count(Member) == 0
