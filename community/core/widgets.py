"""Cards that modules add to the member's own profile page (e.g. "Mein Saldo" from the Kasse).

A module registers a card with @profile_card(template, permission=...); the decorated function
returns the template's context for a member. The profile shows the cards the user may see, in
`order`. "Mein Bereich" (M2-1) builds on this.
"""

from collections.abc import Callable
from typing import NamedTuple

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
