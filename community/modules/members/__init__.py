"""Member directory (/members, /m/<public_id>) and the own profile (/profile).

The members.* permission codes belong to core (community/core/authz/permissions.py),
because the org pages use them too.
"""

from flask import Blueprint

bp = Blueprint('members', __name__, template_folder='templates')

from . import cards, routes  # noqa: E402,F401
