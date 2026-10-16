"""M1-11: local CSS and htmx, the layout's menus, CSRF header for htmx and security headers."""

import hashlib
import json
import os
import re
from pathlib import Path

import pytest
from flask import url_for

from community.core.assets import asset_url
from community.core.nav import ADMIN, MAIN
from community.core.platform.notify import Notification
from community.extensions import db

from .factories import make_admin, make_invitation, make_user

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'community' / 'static'
HTMX_SHA256 = '449317ade7881e949510db614991e195c3a099c4c791c24dacec55f9f4a2a452'  # htmx 1.9.12
CDN_HOSTS = ('cdn.tailwindcss.com', 'unpkg.com', 'cdn.jsdelivr.net')


def _page(client, path='/'):
    return client.get(path).get_data(as_text=True)


# --- Static files and the build ---------------------------------------------------------------


def test_built_css_is_served(client):
    response = client.get('/static/app.css')
    assert response.status_code == 200
    assert response.mimetype == 'text/css'
    assert b'tailwindcss v4' in response.get_data()
    response.close()


def test_vendored_htmx_is_served_and_matches_its_pinned_checksum(client):
    response = client.get('/static/vendor/htmx.min.js')
    assert response.status_code == 200
    assert response.mimetype in ('text/javascript', 'application/javascript')
    assert hashlib.sha256(response.get_data()).hexdigest() == HTMX_SHA256
    response.close()
    # The checksum file that CI checks with `sha256sum -c` says the same.
    assert (STATIC / 'vendor' / 'SHA256SUMS').read_text().split() == [HTMX_SHA256, 'htmx.min.js']


def test_built_css_comes_from_the_pinned_tailwind_version():
    """The committed app.css was built by the CLI version tools/build-css.sh downloads (CI rebuilds it)."""
    script = (ROOT / 'tools' / 'build-css.sh').read_text()
    version = re.search(r'^TAILWIND_VERSION=(\d+\.\d+\.\d+)$', script, re.MULTILINE).group(1)
    assert version.startswith('4.')
    assert re.search(r'tailwindcss-linux-x64\) echo [0-9a-f]{64} ;;', script)
    assert (STATIC / 'app.css').read_text().startswith(f'/*! tailwindcss v{version} ')
    source = (STATIC / 'src' / 'app.css').read_text()
    assert '@import "tailwindcss" source(none);' in source and '@source "../../**/*.html";' in source


def test_templates_load_nothing_from_cdns():
    """N13: no Tailwind Play CDN, no unpkg; scripts and styles come from /static."""
    for path in (ROOT / 'community').rglob('*.html'):
        text = path.read_text()
        for host in CDN_HOSTS:
            assert host not in text, f'{path.relative_to(ROOT)} refers to {host}'
        assert not re.search(r'<(script|link)\b[^>]*\b(src|href)="(https?:)?//', text), path.relative_to(ROOT)


def test_layout_loads_local_assets_with_a_content_hash(client):
    page = _page(client, '/login')
    css_hash = hashlib.sha256((STATIC / 'app.css').read_bytes()).hexdigest()[:12]
    assert f'href="/static/app.css?v={css_hash}"' in page
    assert f'src="/static/vendor/htmx.min.js?v={HTMX_SHA256[:12]}" defer' in page
    for host in CDN_HOSTS:
        assert host not in page
    # htmx only talks to this site and adds no inline <style> (a later CSP need not allow it).
    assert '"selfRequestsOnly": true' in page and '"includeIndicatorStyles": false' in page


def test_asset_url_changes_with_the_file(app, tmp_path):
    asset = tmp_path / 'x.css'
    asset.write_text('a{}')
    app.static_folder = str(tmp_path)
    with app.test_request_context():
        first = asset_url('x.css')
        asset.write_text('b{}')
        mtime = asset.stat().st_mtime_ns
        os.utime(asset, ns=(mtime, mtime + 1_000_000))
        second = asset_url('x.css')
        missing = asset_url('missing.css')
    assert first.startswith('/static/x.css?v=') and second.startswith('/static/x.css?v=')
    assert first != second
    assert missing == '/static/missing.css'


# --- Security headers ----------------------------------------------------------------------------


@pytest.mark.parametrize('path', ['/login', '/', '/does-not-exist', '/static/app.css'])
def test_security_headers_on_every_response(client, path):
    response = client.get(path)
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['Referrer-Policy'] == 'same-origin'
    assert response.headers['X-Frame-Options'] == 'DENY'
    response.close()


def test_security_headers_for_logged_in_pages_and_errors(client, login):
    login(make_user(perms=[]))
    for path, status in (('/profile', 200), ('/members', 403)):
        response = client.get(path)
        assert response.status_code == status
        assert response.headers['X-Frame-Options'] == 'DENY'
        assert response.headers['X-Content-Type-Options'] == 'nosniff'


def test_server_error_page_uses_the_layout_and_sends_the_headers(app, client):
    app.config['PROPAGATE_EXCEPTIONS'] = False

    def boom():
        raise RuntimeError('boom')

    app.add_url_rule('/boom', 'boom', boom)
    response = client.get('/boom')
    assert response.status_code == 500
    page = response.get_data(as_text=True)
    assert 'Da ist etwas schiefgelaufen' in page and '/static/app.css?v=' in page
    assert response.headers['X-Frame-Options'] == 'DENY'


def test_token_pages_keep_their_stricter_referrer_policy(client):
    _, token = make_invitation()
    response = client.get(f'/join/{token}')
    assert response.status_code == 200
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert response.headers['X-Frame-Options'] == 'DENY'


# --- CSRF header for htmx ------------------------------------------------------------------------


def _hx_csrf_token(page):
    return json.loads(re.search(r"<body [^>]*hx-headers='([^']*)'", page).group(1))['X-CSRFToken']


def test_htmx_requests_carry_the_csrf_token_as_header(app, client, login):
    user = make_user()
    login(user)
    app.config['WTF_CSRF_ENABLED'] = True
    token = _hx_csrf_token(_page(client, '/notifications'))
    assert client.post('/notifications/read-all').location == '/'  # no token: refused (core/errors.py)
    response = client.post('/notifications/read-all', headers={'X-CSRFToken': token, 'HX-Request': 'true'})
    assert response.status_code == 302


def test_anonymous_pages_have_the_csrf_header_too(client):
    assert _hx_csrf_token(_page(client, '/login'))


# --- Layout and menus ----------------------------------------------------------------------------


def test_layout_for_anonymous_visitors(client):
    page = _page(client, '/login')
    assert 'href="/login"' in page
    for text in ('Hauptmenü', 'Verwaltung', 'Benachrichtigungen', 'Abmelden', 'action="/logout"', 'href="/members"'):
        assert text not in page
    assert '<main id="main"' in page and 'href="#main"' in page


def test_layout_for_a_member_without_admin_rights(client, login):
    login(make_user(email='me@example.org', name='Max Muster'))  # Bursch
    page = _page(client, '/notifications')  # a page without start page cards that link to the same pages
    for href in ('/announcements', '/chores', '/bookings', '/members', '/org/offices'):
        assert page.count(f'href="{href}"') == 2  # bar for wide screens and the phone menu
    assert 'Verwaltung' not in page
    assert '>Max</a>' in page and 'Mein Profil' in page
    assert page.count('action="/logout"') == 2  # logout is a POST form in both menus
    assert '<details class="group xl:hidden">' in page and 'Menü' in page


def test_member_without_any_permission_sees_only_the_account_menu(client, login):
    login(make_user(perms=[]))
    page = _page(client)
    assert 'href="/profile"' in page and 'href="/notifications"' in page
    for href in ('/announcements', '/chores', '/bookings', '/members', '/org/offices'):
        assert f'href="{href}"' not in page
    assert 'Verwaltung' not in page


def test_admin_sees_the_verwaltung_group(client, login):
    login(make_admin())
    page = _page(client)
    assert page.count('Verwaltung') == 2  # drop-down and phone menu
    for href in (
        '/members/import',
        '/org/semesters',
        '/org/ranks',
        '/bookings/resources',
        '/admin/audit',
        '/admin/jobs',
    ):
        assert f'href="{href}"' in page
    assert 'href="/org/groups"' in page and 'href="/admin/verein"' in page
    assert 'href="/kasse/"' not in page  # full control, but not the Kasse (owner, 2026-10-13)


@pytest.mark.parametrize('link', ADMIN, ids=lambda link: link.endpoint)
def test_each_verwaltung_link_follows_its_route_permission(app, client, login, link):
    access = app.view_functions[link.endpoint]._access
    assert access[0] == 'perm'
    login(make_user(perms=[access[1]]))
    page = _page(client)
    with app.test_request_context():
        href = url_for(link.endpoint)
    assert 'Verwaltung' in page and f'href="{href}"' in page
    assert client.get(href).status_code == 200
    others = [other for other in ADMIN if other.endpoint != link.endpoint]
    with app.test_request_context():
        hidden = [url_for(other.endpoint) for other in others if app.view_functions[other.endpoint]._access != access]
    assert not [h for h in hidden if f'href="{h}"' in page]


@pytest.mark.parametrize('link', MAIN + ADMIN, ids=lambda link: link.endpoint)
def test_menu_links_point_to_existing_routes(app, link):
    for endpoint in (link.endpoint, *link.also):
        assert endpoint in app.view_functions, endpoint
    assert app.view_functions[link.endpoint]._access[0] in ('perm', 'login')


def test_current_page_is_marked_in_the_menu(client, login):
    login(make_user())
    page = _page(client, '/members')
    assert len(re.findall(r'<a href="/members" class="[^"]*" aria-current="page">Mitglieder</a>', page)) == 2
    assert not re.search(r'<a href="/chores" [^>]*aria-current', page)


# --- Unread badge -----------------------------------------------------------------------------


def _add_notifications(member, count):
    db.session.add_all([Notification(member_id=member.id, kind='rank_changed', title=f'N{i}') for i in range(count)])
    db.session.commit()


def test_unread_badge(client, login):
    user = make_user()
    login(user)
    page = _page(client)
    assert 'data-unread' not in page
    assert 'aria-label="Benachrichtigungen"' in page

    _add_notifications(user.member, 3)
    page = _page(client)
    assert page.count('data-unread="3"') == 2  # bell and phone menu
    assert 'aria-label="Benachrichtigungen, 3 ungelesen"' in page


def test_unread_badge_is_capped(client, login):
    user = make_user()
    _add_notifications(user.member, 120)
    login(user)
    page = _page(client)
    assert 'data-unread="120">99+</span>' in page
