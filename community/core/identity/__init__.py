"""People (members) and their login accounts (users)."""

from community.extensions import db, login_manager

from .models import User


@login_manager.user_loader
def load_user(user_token):
    """The token is "id:session_version"; an old version means the login was revoked.

    A disabled account is rejected too, so its sessions end even without a version change.
    """
    user_id, _, version = str(user_token).partition(':')
    if not user_id.isdigit() or not version.isdigit():
        return None
    user = db.session.get(User, int(user_id))
    if user is None or user.session_version != int(version) or not user.is_enabled:
        return None
    return user
