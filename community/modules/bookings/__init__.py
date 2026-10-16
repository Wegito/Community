from flask import Blueprint

from . import permissions  # noqa: F401  (declares this module's permission codes)

bp = Blueprint('bookings', __name__, template_folder='templates')

from . import cards, routes  # noqa: E402,F401
