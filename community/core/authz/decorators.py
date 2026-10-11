"""Every route declares who may use it: @public, @require_login or @require_perm(code).

tests/test_smoke.py fails for a route that has none of them.
"""

from functools import wraps

from flask import abort
from flask_login import login_required

from .registry import PERMISSIONS
from .service import can


def public(view):
    """Open to anyone, also without login (login page, invitation links)."""
    view._access = ('public', None)
    return view


def require_login(view):
    """Any logged-in account (own profile, own password, logout)."""
    wrapped = login_required(view)
    wrapped._access = ('login', None)
    return wrapped


def require_perm(code):
    """Logged in and holding permission `code`; otherwise 403."""
    if code not in PERMISSIONS:
        raise ValueError(f'Permission {code!r} is not declared; add it to a permissions.py')

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not can(code):
                abort(403)
            return view(*args, **kwargs)

        wrapped._access = ('perm', code)
        return wrapped

    return decorator
