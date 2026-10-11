import pytest

from community import create_app
from community.config import DevelopmentConfig, ProductionConfig


def test_app_registers_all_routes(app):
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
    assert {
        'auth.login',
        'auth.register',
        'auth.logout',
        'announcements.index',
        'announcements.list',
        'announcements.create',
        'tasks.list',
        'tasks.edit',
        'bookings.calendar',
    } <= endpoints


@pytest.mark.parametrize('key', ['', 'dev-secret', 'short-but-not-default'])
def test_production_refuses_weak_secret_key(key):
    class Config(ProductionConfig):
        SECRET_KEY = key

    with pytest.raises(RuntimeError, match='SECRET_KEY'):
        create_app(Config)


def test_production_starts_with_strong_secret_key(tmp_path):
    class Config(ProductionConfig):
        SECRET_KEY = 'x' * 64
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{tmp_path / "prod.db"}'

    app = create_app(Config)
    assert app.config['SESSION_COOKIE_SECURE'] is True
    assert app.config['SESSION_COOKIE_HTTPONLY'] is True


def test_unknown_app_env_is_rejected():
    with pytest.raises(RuntimeError, match='APP_ENV'):
        create_app('staging')


def test_proxy_fix_only_when_hops_configured(tmp_path):
    class NoProxy(DevelopmentConfig):
        TRUSTED_PROXY_HOPS = 0

    class OneProxy(DevelopmentConfig):
        TRUSTED_PROXY_HOPS = 1

    assert type(create_app(NoProxy).wsgi_app).__name__ != 'ProxyFix'
    assert type(create_app(OneProxy).wsgi_app).__name__ == 'ProxyFix'
