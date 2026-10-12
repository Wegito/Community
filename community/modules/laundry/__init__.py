"""Laundry (Wäsche): log washes and dryer runs from a QR code; monthly overview with prices."""

from flask import Blueprint

from . import permissions  # noqa: F401  (declares this module's permission codes)

bp = Blueprint('laundry', __name__, url_prefix='/waesche', template_folder='templates')

from . import cli, routes  # noqa: E402,F401
