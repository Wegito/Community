import shutil

import pytest
from flask.testing import FlaskClient
from flask_migrate import upgrade

from community import create_app
from community.config import TestingConfig
from community.core.authz.service import seed_grants, sync_permissions
from community.extensions import db

from .factories import DEFAULT_PASSWORD


class FreshContextClient(FlaskClient):
    """Run each request in its own app context, as in production.

    Otherwise requests reuse the fixture's app context: Flask-Login would keep the
    user cached in `g`, and the session cookie and user loader would never be tested.
    """

    def open(self, *args, **kwargs):
        with self.application.app_context():
            return super().open(*args, **kwargs)


def _config_for(path):
    class Config(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{path}'

    return Config


@pytest.fixture(scope='session')
def template_db(tmp_path_factory):
    """One database per test run, built exactly as in production (migrations, sync, seed)."""
    path = tmp_path_factory.mktemp('template') / 'template.db'
    app = create_app(_config_for(path))
    with app.app_context():
        upgrade()
        sync_permissions()
        seed_grants()
        db.session.remove()
        db.engine.dispose()
    return path


@pytest.fixture
def app(tmp_path, template_db):
    """A fresh copy of the template database for every test."""
    path = tmp_path / 'test.db'
    shutil.copy(template_db, path)
    app = create_app(_config_for(path))
    app.test_client_class = FreshContextClient
    with app.app_context():
        yield app
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def login(client):
    """Log a user in through the real login form: login(user) or login(email, password)."""

    def do_login(user_or_email, password=DEFAULT_PASSWORD):
        email = getattr(user_or_email, 'login_email', user_or_email)
        return client.post('/login', data={'email': email, 'password': password})

    return do_login
