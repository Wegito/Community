"""Community: the club's internal web app."""

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from community.extensions import db, login_manager

# Blueprints in registration order. A new module is added here (roadmap §4.4).
MODULES = [
    'community.modules.auth',
    'community.modules.announcements',
    'community.modules.tasks',
    'community.modules.bookings',
]


def create_app(config=None):
    from importlib import import_module

    from community.config import Config

    app = Flask(__name__)
    app.config.from_object(config or Config)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'

    import community.core.identity  # noqa: F401  (registers the user loader)
    from community.core.identity.cli import create_admin

    app.cli.add_command(create_admin)

    for name in MODULES:
        app.register_blueprint(import_module(name).bp)

    return app
