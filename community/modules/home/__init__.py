"""Startseite (M2-1): what is new for me, what waits for my Ämter, and the Bund in numbers.

The page has no data of its own: every module registers its cards with core.widgets.home_card.
"""

from flask import Blueprint

bp = Blueprint('home', __name__, template_folder='templates')

from . import cards, routes  # noqa: E402,F401
