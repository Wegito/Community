"""System administration pages: audit log and background jobs."""

from flask import Blueprint

bp = Blueprint('admin', __name__, url_prefix='/admin', template_folder='templates')

from . import cards, routes  # noqa: E402,F401
