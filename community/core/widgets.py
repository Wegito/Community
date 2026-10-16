"""Cards that modules add to the member's profile page and to the start page.

A module registers a card with @profile_card(template, permission=...) or @home_card(...); the
decorated function returns the template's context for a member. Pages show the cards the user
may see, in `order`. The start page (M2-1) is built only from these cards, so a new module adds
its own cards without touching the start page.
"""

import logging
from collections.abc import Callable
from typing import NamedTuple

from flask import current_app

from community.core.authz import can


class ProfileCard(NamedTuple):
    template: str
    permission: str
    order: int
    context: Callable


_cards = []


def profile_card(template, *, permission, order=100):
    def register(context):
        _cards.append(ProfileCard(template, permission, order, context))
        return context

    return register


def profile_cards(member):
    """[(template, context)] for the current user's own profile."""
    return [
        (card.template, card.context(member)) for card in sorted(_cards, key=lambda c: c.order) if can(card.permission)
    ]


# --- Start page (M2-1) ---------------------------------------------------------------------

MAIN = 'main'  # the member's own things: Saldo, Aufgaben, Wäsche, ...
OFFICE = 'office'  # work for holders of an Amt: what is waiting for them
CLUB = 'club'  # the Bund as a whole

log = logging.getLogger(__name__)


class HomeCard(NamedTuple):
    template: str
    permission: str | None
    order: int
    area: str
    context: Callable


_home_cards = []


def home_card(template, *, permission=None, order=100, area=MAIN):
    """Register a card for the start page. The function gets the member and returns the template's
    context, or None when the card has nothing to say (then it is left out, no empty boxes).
    permission=None: every logged-in member."""
    if area not in (MAIN, OFFICE, CLUB):
        raise ValueError(f'Unknown start page area {area!r}')

    def register(context):
        _home_cards.append(HomeCard(template, permission, order, area, context))
        return context

    return register


def home_cards(member):
    """{area: [(template, context)]} of the cards the current user may see and that have content.

    A card that fails is left out and logged, so one broken module cannot break the start page
    (in tests the error is raised instead).
    """
    result = {MAIN: [], OFFICE: [], CLUB: []}
    for card in sorted(_home_cards, key=lambda c: c.order):
        if card.permission is not None and not can(card.permission):
            continue
        try:
            data = card.context(member)
        except Exception:
            if current_app.testing:
                raise
            log.exception('Start page card %s failed', card.template)
            continue
        if data is not None:
            result[card.area].append((card.template, data))
    return result
