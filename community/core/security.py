"""Access helpers shared by all modules."""

from functools import wraps
from urllib.parse import urlsplit

from flask import flash, redirect, url_for
from flask_login import current_user, login_required

from community.core.identity.models import Role


def safe_next(target):
    """Return `target` if it is a safe local path to redirect to, else None.

    Only paths like "/chores?x=1" are allowed: no scheme or host, no "//" or
    backslash (browsers read "/\\evil.org" as "//evil.org"), no control characters.
    """
    if not target or not target.startswith('/') or target.startswith('//'):
        return None
    if '\\' in target or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in target):
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return None
    return target


def admin_required(view):
    """Allow only logged-in admins; others are redirected with a message.

    Replaced by permission checks (@require_perm) in roadmap task M1-3.
    """

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user.role != Role.ADMIN:
            flash('Nur für Admins', 'warning')
            return redirect(url_for('announcements.index'))
        return view(*args, **kwargs)

    return wrapped
