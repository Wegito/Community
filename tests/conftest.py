import pytest
from flask_migrate import upgrade

from community import create_app
from community.config import TestingConfig
from community.extensions import db

from .factories import DEFAULT_PASSWORD


@pytest.fixture
def app(tmp_path):
    class Config(TestingConfig):
        # A real file, so the schema is built exactly as in production: through the migrations.
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{tmp_path / "test.db"}'

    app = create_app(Config)
    with app.app_context():
        upgrade()
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
        email = getattr(user_or_email, 'email', user_or_email)
        return client.post('/login', data={'email': email, 'password': password})

    return do_login
