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


@pytest.fixture
def app(tmp_path):
    class Config(TestingConfig):
        # A real file, so the schema is built exactly as in production: through the migrations.
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{tmp_path / "test.db"}'

    app = create_app(Config)
    app.test_client_class = FreshContextClient
    with app.app_context():
        upgrade()
        sync_permissions()
        seed_grants()
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
