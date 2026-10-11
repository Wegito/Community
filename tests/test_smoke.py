"""Every route answers: anonymous visitors are redirected, members get a page."""

import pytest

from .factories import make_user

PROTECTED_GET = ['/announcements', '/chores', '/bookings']
PUBLIC_GET = ['/login', '/register']


@pytest.mark.parametrize('url', PUBLIC_GET)
def test_public_pages_render(client, url):
    assert client.get(url).status_code == 200


@pytest.mark.parametrize('url', PROTECTED_GET)
def test_protected_pages_redirect_to_login(client, url):
    response = client.get(url)
    assert response.status_code == 302
    assert '/login' in response.location


@pytest.mark.parametrize('url', PROTECTED_GET)
def test_protected_pages_render_for_members(client, login, url):
    make_user()
    login('max@example.org')
    assert client.get(url).status_code == 200
