"""Kasse (finance): Kassen, bookings, every member's Kontokarte (Saldo), cost centres.

Structured like the Kassenwart's Excel journal (docs/spikes/m3-0-kasse-excel.md), but every
booking must balance before it is saved, and nothing is ever edited: mistakes are reversed.
"""

from flask import Blueprint

# Permission codes, tables, and the guard that keeps bookings from being edited.
from . import models, permissions, services  # noqa: F401

bp = Blueprint('finance', __name__, url_prefix='/kasse', template_folder='templates')

from . import routes  # noqa: E402,F401
