"""Form errors are German: WTForms' own messages come from its bundled German translations."""

import flask_wtf

from .factories import make_admin

ENGLISH = ('This field is required.', 'Invalid email address.', 'Not a valid choice.', 'Not a valid date value.')


def test_default_validator_messages_are_german(client, login):
    login(make_admin())
    response = client.post(
        '/members/new',
        data={'first_name': '', 'email': 'kein-email', 'joined_on': '', 'rank_id': '999999', 'delivery': 'link'},
    )
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'Dieses Feld wird benötigt.' in page  # Vorname and date missing
    assert 'Ungültige E-Mail-Adresse.' in page
    assert 'Keine gültige Auswahl.' in page  # a rank that is not offered
    for message in ENGLISH:
        assert message not in page


def test_german_messages_on_public_forms(client):
    response = client.post('/login', data={'email': 'quirin', 'password': ''})
    page = response.get_data(as_text=True)
    assert 'Ungültige E-Mail-Adresse.' in page and 'Dieses Feld wird benötigt.' in page
    assert not any(message in page for message in ENGLISH)


def _subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _subclasses(sub)


def test_every_form_uses_the_german_base_form(app):
    from community.core.forms import FlaskForm

    ours = [form for form in _subclasses(flask_wtf.FlaskForm) if form.__module__.startswith('community.')]
    assert len(ours) > 20
    assert [form.__qualname__ for form in ours if not issubclass(form, FlaskForm)] == []
