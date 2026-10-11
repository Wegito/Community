"""Access helpers shared by all modules."""

from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user, login_required

from community.core.identity.models import Role


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
