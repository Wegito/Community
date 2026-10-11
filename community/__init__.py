"""Community: the club's internal web app."""

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from community.extensions import csrf, db, login_manager, migrate

# Blueprints in registration order. A new module is added here (roadmap §4.4).
MODULES = [
    'community.modules.auth',
    'community.modules.announcements',
    'community.modules.tasks',
    'community.modules.bookings',
]


def create_app(config=None):
    """Build the app. `config` is a config class, an APP_ENV name, or None (read APP_ENV)."""
    from importlib import import_module

    from community.config import get_config

    config_class = config if isinstance(config, type) else get_config(config)
    config_class.validate()

    app = Flask(__name__)
    app.config.from_object(config_class)

    hops = app.config['TRUSTED_PROXY_HOPS']
    if hops > 0:
        # Host is not taken from X-Forwarded-Host: Nginx passes it as the Host header.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops)

    import community.core.db  # noqa: F401  (SQLite connection settings)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Bitte einloggen.'
    login_manager.login_message_category = 'warning'

    import community.core.identity  # noqa: F401  (registers the user loader)
    from community.core.identity.cli import create_admin

    app.cli.add_command(create_admin)

    for name in MODULES:
        app.register_blueprint(import_module(name).bp)

    from community.core.errors import register_error_handlers
    from community.core.money import format_cents
    from community.core.time import format_date, format_datetime

    register_error_handlers(app)
    app.jinja_env.filters.update(dt=format_datetime, d=format_date, cents=format_cents)

    @app.context_processor
    def inject_club_name():
        return {'club_name': app.config['CLUB_NAME']}

    return app
