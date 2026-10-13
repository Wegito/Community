"""The club's own data: its name, a short name for the menu bar, and what it calls its Heimverein.

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
HEIMVEREIN_MAX = 80


class Club(db.Model):
    """One row (id 1) with the club's settings."""

    __table_args__ = (db.CheckConstraint('id = 1', name='single_row'),)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(NAME_MAX), nullable=False)
    short_name = db.Column(db.String(SHORT_NAME_MAX))  # menu bar; empty = the name
    # The association that owns the house, by the name this club uses for it. Optional: not every
    # club has one. The Kasse (M3) books what it pays for or owes under this name.
    heimverein = db.Column(db.String(HEIMVEREIN_MAX))
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


def heimverein_name():
    """The Heimverein's name, or None when the club has none."""
    club = saved_club()
    return club.heimverein if club is not None else None


def update_club(name, short_name=None, heimverein=None, *, actor):
    """Save the club's settings. Returns {field: [old, new]}. Commits nothing."""
    name = (name or '').strip()
    short_name = (short_name or '').strip() or None
    heimverein = (heimverein or '').strip() or None
    if not name:
        raise ValueError('Der Name darf nicht leer sein.')
    club = saved_club()
    created = club is None
    before = {
        'name': club_name(),
        'short_name': None if created else club.short_name,
        'heimverein': None if created else club.heimverein,
    }
    if created:
        club = Club(id=1, name=name)
        db.session.add(club)
    club.name, club.short_name, club.heimverein = name, short_name, heimverein
    diff = audit.changes(club, ('name', 'short_name', 'heimverein'), before)
    if diff or created:
        db.session.flush()
        audit.record('club.update', club, actor=actor, diff=diff)
    g._club = club
    return diff
