"""People and their login accounts."""

from community.extensions import db, login_manager

from .models import User


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))
