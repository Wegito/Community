"""Start page card: the newest announcements."""

from sqlalchemy import select

from community.core.widgets import MAIN, home_card
from community.extensions import db

from .models import Announcement

LIMIT = 3


@home_card('announcements/_home_latest.html', permission='announcements.view', order=50, area=MAIN)
def latest_card(member):
    items = db.session.scalars(select(Announcement).order_by(Announcement.created_at.desc()).limit(LIMIT)).all()
    return {'items': items} if items else None
