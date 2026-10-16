from datetime import UTC, date, datetime

import pytest

from community.core.money import format_cents, parse_cents
from community.core.time import format_date, format_datetime, local_to_utc, to_local, utcnow


def test_utcnow_is_aware():
    assert utcnow().tzinfo is UTC


def test_local_to_utc_handles_summer_and_winter_time():
    assert local_to_utc(datetime(2026, 7, 1, 20, 0)) == datetime(2026, 7, 1, 18, 0, tzinfo=UTC)
    assert local_to_utc(datetime(2026, 12, 1, 20, 0)) == datetime(2026, 12, 1, 19, 0, tzinfo=UTC)


def test_to_local_and_format():
    value = datetime(2026, 10, 11, 18, 30, tzinfo=UTC)
    assert to_local(value).hour == 20
    assert format_datetime(value) == '11.10.2026 20:30'
    assert format_datetime(None) == ''
    assert format_date(date(2026, 1, 2)) == '02.01.2026'
    assert format_date(None) == ''


@pytest.mark.parametrize(
    ('text', 'cents'),
    [('12,50', 1250), ('12.50', 1250), ('-3', -300), ('1.234,56 €', 123456), ('12.29', 1229), (7, 700)],
)
def test_parse_cents(text, cents):
    assert parse_cents(text) == cents


@pytest.mark.parametrize('text', ['abc', '', 'NaN', 'inf', '1.234', '1.234 €', '1,234.56', '12.345', '0,001'])
def test_parse_cents_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_cents(text)


@pytest.mark.parametrize(('cents', 'text'), [(123456, '1.234,56 €'), (-50, '-0,50 €'), (0, '0,00 €')])
def test_format_cents(cents, text):
    assert format_cents(cents) == text


def test_utc_datetime_column_rejects_naive_values(app):
    from community.extensions import db
    from tests.factories import make_user

    user = make_user()
    user.created_at = datetime(2026, 1, 1, 12, 0)
    with pytest.raises(Exception, match='timezone-aware'):
        db.session.commit()
    db.session.rollback()


def test_utc_datetime_round_trip(app):
    from community.extensions import db
    from tests.factories import make_user

    user = make_user()
    db.session.expire_all()
    assert db.session.get(type(user), user.id).created_at.tzinfo is UTC


def test_unknown_page_shows_404_page(client):
    response = client.get('/does-not-exist')
    assert response.status_code == 404
    assert 'Nicht gefunden' in response.get_data(as_text=True)


def test_club_name_is_shown(app, client):
    app.config['CLUB_NAME'] = 'VDSt Test'
    assert 'VDSt Test' in client.get('/login').get_data(as_text=True)
