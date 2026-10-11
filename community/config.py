"""Configuration per environment.

APP_ENV selects the class: development, testing or production (default).
Values come from environment variables. The entry points load .env first:
wsgi.py does it explicitly, and the `flask` command does it by itself.
"""

import os
from datetime import timedelta

# Values that must never be used as a real secret key.
WEAK_SECRET_KEYS = {'', 'dev-secret', 'change-me', 'secret', 'changeme'}
MIN_SECRET_KEY_LENGTH = 32


def env_bool(name, default):
    value = os.getenv(name)
    if value is None or value.strip() == '':
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def env_str(name, default):
    value = os.getenv(name)
    if value is None or value.strip() == '':
        return default
    return value.strip()


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
    PREFERRED_URL_SCHEME = env_str('PREFERRED_URL_SCHEME', 'https')

    # Number of reverse proxies whose X-Forwarded-* headers are trusted.
    # 0 = no proxy (headers ignored), 1 = behind one Nginx.
    TRUSTED_PROXY_HOPS = env_int('TRUSTED_PROXY_HOPS', 0)

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = env_bool('SESSION_COOKIE_SECURE', True)
    SESSION_COOKIE_SAMESITE = env_str('SESSION_COOKIE_SAMESITE', 'Lax')
    REMEMBER_COOKIE_DURATION = timedelta(days=90)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_SAMESITE = SESSION_COOKIE_SAMESITE

    # Login throttling, counted per account in the database (works across workers).
    MAX_FAILED_LOGINS = env_int('MAX_FAILED_LOGINS', 5)
    LOGIN_LOCKOUT_MINUTES = env_int('LOGIN_LOCKOUT_MINUTES', 15)

    # IP addresses in the audit log are removed after this many days (flask audit purge-ips).
    AUDIT_IP_RETENTION_DAYS = env_int('AUDIT_IP_RETENTION_DAYS', 90)

    # Background jobs (flask jobs tick): a failing job is retried with backoff, then marked failed.
    JOB_MAX_ATTEMPTS = env_int('JOB_MAX_ATTEMPTS', 5)

    # Mail: console (log only), memory (tests) or smtp. Empty = smtp if MAIL_SERVER is set, else console.
    MAIL_BACKEND = env_str('MAIL_BACKEND', '')
    MAIL_SERVER = env_str('MAIL_SERVER', '')
    MAIL_USE_SSL = env_bool('MAIL_USE_SSL', False)  # implicit TLS, usually port 465
    MAIL_USE_TLS = env_bool('MAIL_USE_TLS', not MAIL_USE_SSL)  # STARTTLS, usually port 587
    MAIL_PORT = env_int('MAIL_PORT', 465 if MAIL_USE_SSL else 587)
    MAIL_USERNAME = env_str('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD', '')
    MAIL_FROM = env_str('MAIL_FROM', 'Community <noreply@localhost>')

    @classmethod
    def validate(cls):
        """Raise RuntimeError when the configuration is unsafe to run."""
        if str(cls.SESSION_COOKIE_SAMESITE).title() not in {'Strict', 'Lax', 'None'}:
            raise RuntimeError('SESSION_COOKIE_SAMESITE must be Strict, Lax or None')
        if cls.MAIL_BACKEND not in {'', 'console', 'memory', 'smtp'}:
            raise RuntimeError('MAIL_BACKEND must be console, memory or smtp (or empty)')
        if cls.MAIL_BACKEND == 'smtp' and not cls.MAIL_SERVER:
            raise RuntimeError('MAIL_BACKEND=smtp needs MAIL_SERVER')
        if cls.MAIL_USE_SSL and cls.MAIL_USE_TLS:
            raise RuntimeError('Set either MAIL_USE_SSL (port 465) or MAIL_USE_TLS (STARTTLS, port 587), not both')
        if cls.JOB_MAX_ATTEMPTS < 1:
            raise RuntimeError('JOB_MAX_ATTEMPTS must be at least 1')


class DevelopmentConfig(BaseConfig):
    ENV_NAME = 'development'
    DEBUG = True
    SECRET_KEY = os.getenv('SECRET_KEY') or 'dev-only-insecure-key'
    PREFERRED_URL_SCHEME = env_str('PREFERRED_URL_SCHEME', 'http')
    SESSION_COOKIE_SECURE = env_bool('SESSION_COOKIE_SECURE', False)
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    MAIL_BACKEND = env_str('MAIL_BACKEND', 'console')


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
    BASE_URL = ''
    MAIL_BACKEND = 'memory'
    MAIL_SERVER = ''
    MAIL_USE_SSL = False
    MAIL_USE_TLS = True
    MAIL_FROM = 'Community <noreply@localhost>'
    JOB_MAX_ATTEMPTS = 5


class ProductionConfig(BaseConfig):
    ENV_NAME = 'production'

    @classmethod
    def validate(cls):
        super().validate()
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
