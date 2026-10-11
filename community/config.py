"""Configuration per environment.

APP_ENV selects the class: development, testing or production (default).
Values come from environment variables, optionally loaded from a .env file.
"""

import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

# Values that must never be used as a real secret key.
WEAK_SECRET_KEYS = {'', 'dev-secret', 'change-me', 'secret', 'changeme'}
MIN_SECRET_KEY_LENGTH = 32


def env_bool(name, default):
    value = os.getenv(name)
    if value is None or value.strip() == '':
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def env_int(name, default):
    value = os.getenv(name)
    if value is None or value.strip() == '':
        return default
    return int(value)


class BaseConfig:
    ENV_NAME = 'base'
    SECRET_KEY = os.getenv('SECRET_KEY', '')
    SQLALCHEMY_DATABASE_URI = os.getenv('SQLALCHEMY_DATABASE_URI', 'sqlite:///community.db')
    SQLALCHEMY_ECHO = False
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Shared registration code; empty = registration closed. Removed in M1-7.
    INVITE_CODE = os.getenv('INVITE_CODE', '')

    CLUB_NAME = os.getenv('CLUB_NAME', 'Community')
    BASE_URL = os.getenv('BASE_URL', '').rstrip('/')
    PREFERRED_URL_SCHEME = os.getenv('PREFERRED_URL_SCHEME', 'https')

    # Number of reverse proxies whose X-Forwarded-* headers are trusted.
    # 0 = no proxy (headers ignored), 1 = behind one Nginx.
    TRUSTED_PROXY_HOPS = env_int('TRUSTED_PROXY_HOPS', 0)

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = env_bool('SESSION_COOKIE_SECURE', True)
    SESSION_COOKIE_SAMESITE = os.getenv('SESSION_COOKIE_SAMESITE', 'Lax')
    REMEMBER_COOKIE_DURATION = timedelta(days=90)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_SAMESITE = SESSION_COOKIE_SAMESITE

    @classmethod
    def validate(cls):
        """Raise RuntimeError when the configuration is unsafe to run."""


class DevelopmentConfig(BaseConfig):
    ENV_NAME = 'development'
    DEBUG = True
    SECRET_KEY = os.getenv('SECRET_KEY') or 'dev-only-insecure-key'
    PREFERRED_URL_SCHEME = os.getenv('PREFERRED_URL_SCHEME', 'http')
    SESSION_COOKIE_SECURE = env_bool('SESSION_COOKIE_SECURE', False)
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE


class TestingConfig(BaseConfig):
    ENV_NAME = 'testing'
    TESTING = True
    SECRET_KEY = 'testing-key'  # noqa: S105 (tests only)
    SQLALCHEMY_DATABASE_URI = 'sqlite://'
    WTF_CSRF_ENABLED = False
    INVITE_CODE = 'test-invite'
    PREFERRED_URL_SCHEME = 'http'
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    TRUSTED_PROXY_HOPS = 0


class ProductionConfig(BaseConfig):
    ENV_NAME = 'production'

    @classmethod
    def validate(cls):
        key = cls.SECRET_KEY or ''
        if key.strip().lower() in WEAK_SECRET_KEYS or len(key) < MIN_SECRET_KEY_LENGTH:
            raise RuntimeError(
                'SECRET_KEY is missing or weak. Set a random value of at least '
                f'{MIN_SECRET_KEY_LENGTH} characters, e.g. '
                '`python -c "import secrets; print(secrets.token_hex(32))"`.'
            )


CONFIGS = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
}


def get_config(name=None):
    name = (name or os.getenv('APP_ENV') or 'production').strip().lower()
    try:
        return CONFIGS[name]
    except KeyError:
        raise RuntimeError(f'Unknown APP_ENV {name!r}; use one of {", ".join(CONFIGS)}') from None
