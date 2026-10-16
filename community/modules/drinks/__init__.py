"""Getränke (M6): the Bierwart's Strichliste, every member's Deckel, Bierkönig and Fass-Barometer,
and the API for the bar tablet. Billing onto the Kontokarte follows once the Bierwart's Excel is
known (M6-5)."""

from flask import Blueprint

from . import models, permissions  # noqa: F401

bp = Blueprint('drinks', __name__, url_prefix='/getraenke', template_folder='templates')

from . import api, cards, routes  # noqa: E402,F401
