"""The club's own data: its name, and a short name for the menu bar.

Editable in the app (Verwaltung → Verein, `system.admin`), so another club can run its own copy
without touching the code. Until someone saves it, the name comes from CLUB_NAME in .env.
"""

from flask import current_app, g
from sqlalchemy import select

from community.core.db import UTCDateTime
from community.core.platform import audit
from community.core.time import utcnow
from community.extensions import db

NAME_MAX = 80
SHORT_NAME_MAX = 24


class Club(db.Model):
    """One row (id 1) with the club's settings."""

    __table_args__ = (db.CheckConstraint('id = 1', name='single_row'),)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(NAME_MAX), nullable=False)
    short_name = db.Column(db.String(SHORT_NAME_MAX))  # menu bar; empty = the name
    updated_at = db.Column(UTCDateTime, default=utcnow, onupdate=utcnow)


def saved_club():
    """The saved row or None; looked up once per request or job."""
    if '_club' not in g:
        g._club = db.session.scalar(select(Club).limit(1))
    return g._club


def club_name():
    club = saved_club()
    return club.name if club is not None else current_app.config['CLUB_NAME']


def club_short_name():
    """For the menu bar, where a long name gets cut off."""
    club = saved_club()
    if club is not None and club.short_name:
        return club.short_name
    return club_name()


def update_club(name, short_name=None, *, actor):
    """Save the club's name and short name. Returns {field: [old, new]}. Commits nothing."""
    name = (name or '').strip()
    short_name = (short_name or '').strip() or None
    if not name:
        raise ValueError('Der Name darf nicht leer sein.')
    club = saved_club()
    created = club is None
    before = {'name': club_name(), 'short_name': None if created else club.short_name}
    if created:
        club = Club(id=1, name=name)
        db.session.add(club)
    club.name, club.short_name = name, short_name
    diff = audit.changes(club, ('name', 'short_name'), before)
    if diff or created:
        db.session.flush()
        audit.record('club.update', club, actor=actor, diff=diff)
    g._club = club
    return diff
