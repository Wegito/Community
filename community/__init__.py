"""Community: the club's internal web app."""

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from community.extensions import csrf, db, login_manager, migrate

# Blueprints in registration order. A new module is added here (roadmap §4.4).
MODULES = [
    'community.modules.auth',
    'community.modules.members',
    'community.modules.announcements',
    'community.modules.tasks',
    'community.modules.bookings',
    'community.modules.admin',
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

    import community.core.authz  # noqa: F401  (declares core permission codes)
    import community.core.identity  # noqa: F401  (registers the user loader)
    import community.core.org.services  # noqa: F401  (adds rank, office and group roles to authz)
    from community.core.authz.cli import authz_cli
    from community.core.bootstrap import setup
    from community.core.identity.cli import create_admin
    from community.core.org.cli import org_cli
    from community.core.org.routes import bp as org_bp
    from community.core.platform.audit import audit_cli, init_request_ids
    from community.core.platform.jobs import jobs_cli
    from community.core.platform.mail import mail_cli
    from community.core.platform.notify import current_unread_count
    from community.core.platform.routes import bp as notifications_bp

    app.cli.add_command(setup)
    app.cli.add_command(create_admin)
    app.cli.add_command(audit_cli)
    app.cli.add_command(authz_cli)
    app.cli.add_command(org_cli)
    app.cli.add_command(jobs_cli)
    app.cli.add_command(mail_cli)
    app.register_blueprint(org_bp)
    app.register_blueprint(notifications_bp)
    init_request_ids(app)

    for name in MODULES:
        app.register_blueprint(import_module(name).bp)

    from community.core.errors import register_error_handlers
    from community.core.money import format_cents
    from community.core.security import init_security_headers
    from community.core.time import format_date, format_datetime

    register_error_handlers(app)
    init_security_headers(app)
    app.jinja_env.filters.update(dt=format_datetime, d=format_date, cents=format_cents)

    from community.core.assets import asset_url
    from community.core.authz import can, policies
    from community.core.nav import may_open, nav_links

    @app.context_processor
    def inject_globals():
        return {
            'club_name': app.config['CLUB_NAME'],
            'asset_url': asset_url,
            'can': can,
            'policies': policies,
            'nav_links': nav_links,
            'may_open': may_open,
            'unread_notifications': current_unread_count,
        }

    return app
