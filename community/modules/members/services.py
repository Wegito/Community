"""Directory queries, who sees which contact data, and editing member data."""

import unicodedata
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from community.core.identity.models import Member, MemberStatus, User, normalize_email
from community.core.org import services as org
from community.core.platform import audit
from community.core.time import today_local
from community.extensions import db

# What holders of members.edit_any may change, and what every member may change on their own profile.
ADMIN_FIELDS = (
    'first_name',
    'last_name',
    'biername',
    'email',
    'phone',
    'address',
    'joined_on',
    'show_phone',
    'show_address',
)
OWN_FIELDS = ('biername', 'phone', 'address', 'show_phone', 'show_address')

# Text fields stored as NULL when empty (email is unique, so '' must never be stored).
_NULLABLE_TEXT = {'biername', 'email', 'phone', 'address'}

EMAIL_IN_USE = 'Diese E-Mail-Adresse gehört schon zu einem anderen Mitglied.'


class MemberError(Exception):
    """A rule was broken; the message is shown to the user (German)."""


class EmailInUse(MemberError):
    """The address belongs to another member (contact or login email)."""


# --- Directory -------------------------------------------------------------------


class DirectoryRow(NamedTuple):
    member: Member
    rank_entry: object  # RankHistory or None
    offices: list  # OfficeAssignment


def fold(text):
    """Lowercase without accents, so "Özil" sorts with O and "oz" or "OZ" finds it (DIN 5007-1)."""
    decomposed = unicodedata.normalize('NFKD', text or '')
    return ''.join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _matches(member, terms):
    haystack = fold(' '.join(filter(None, (member.first_name, member.last_name, member.biername))))
    return all(term in haystack for term in terms)


def directory(q=None, rank_key=None, include_ended=False, day=None):
    """Members sorted by first and last name, each with their rank and Ämter on `day`.

    q: every word must appear in the first name, last name or Biername (case and accents ignored).
    rank_key: only members whose current rank has this key.
    include_ended: also former members (status ENDED); only for members.edit_any holders.

    Names are matched in Python: SQLite's LOWER() and LIKE fold only ASCII letters ("Ö" != "ö"),
    and the club is small enough to load everyone.
    """
    day = day or today_local()
    query = select(Member)
    if not include_ended:
        query = query.where(Member.status == MemberStatus.ACTIVE)
    members = db.session.scalars(query).all()

    terms = fold(q).split() if q else []
    ranks = org.current_rank_entries(day)
    offices = {}
    for assignment in org.office_assignments_on(day):
        offices.setdefault(assignment.member_id, []).append(assignment)

    rows = []
    for member in members:
        entry = ranks.get(member.id)
        if rank_key and (entry is None or entry.rank.key != rank_key):
            continue
        if terms and not _matches(member, terms):
            continue
        rows.append(DirectoryRow(member, entry, offices.get(member.id, [])))
    rows.sort(key=lambda row: (fold(row.member.first_name), fold(row.member.last_name), row.member.id))
    return rows


def member_by_public_id(public_id):
    return db.session.scalar(select(Member).where(Member.public_id == public_id))


class Overview(NamedTuple):
    rank_entry: object  # RankHistory or None
    offices: list  # OfficeAssignment
    groups: list  # GroupMembership


def overview(member, day=None):
    """Rank (with its start date), Ämter and groups on `day`, for the profile pages."""
    groups = [m for m in org.member_groups(member, day) if m.group.archived_at is None]
    groups.sort(key=lambda m: fold(m.group.name))
    return Overview(org.current_rank_entry(member, day), org.member_offices(member, day), groups)


# --- Contact data ----------------------------------------------------------------


def contact_email(member):
    """The address shown to others: the contact email, else the login email."""
    if member.email:
        return member.email
    return member.user.login_email if member.user is not None else None


def visible_contact(member, permissions, *, is_self=False):
    """The contact data a viewer holding `permissions` may see; hidden fields are None.

    The member themselves and members.edit_any holders see everything. members.contact.view
    shows the email, the phone only if the member set show_phone, the address only with
    show_address. Without members.contact.view nothing is shown. Never Saldo or finance data.
    """
    data = {'email': contact_email(member), 'phone': member.phone, 'address': member.address}
    if is_self or 'members.edit_any' in permissions:
        return data
    if 'members.contact.view' not in permissions:
        return dict.fromkeys(data)
    return {
        'email': data['email'],
        'phone': member.phone if member.show_phone else None,
        'address': member.address if member.show_address else None,
    }


# --- Editing ---------------------------------------------------------------------


def email_in_use(email, member):
    """True if another member has this contact email, or another member's login uses it."""
    contact = select(Member.id).where(Member.email == email, Member.id != member.id)
    login = select(User.id).where(User.login_email == email, User.member_id != member.id)
    return db.session.scalar(contact.limit(1)) is not None or db.session.scalar(login.limit(1)) is not None


def _clean(field, value):
    if field == 'email':
        value = normalize_email(value)
    elif isinstance(value, str):
        value = value.strip()
    if field in _NULLABLE_TEXT and not value:
        return None
    if field in ('show_phone', 'show_address'):
        return bool(value)
    return value


def _update(member, data, allowed, action, actor):
    unknown = set(data) - set(allowed)
    if unknown:
        raise ValueError(f'Fields not editable here: {", ".join(sorted(unknown))}')
    values = {field: _clean(field, value) for field, value in data.items()}
    if 'first_name' in values and not values['first_name']:
        raise MemberError('Der Vorname darf nicht leer sein.')
    if 'last_name' in values and values['last_name'] is None:
        values['last_name'] = ''
    email = values.get('email')
    if email and email != member.email and email_in_use(email, member):
        raise EmailInUse(EMAIL_IN_USE)

    fields = list(values)
    before = audit.snapshot(member, fields)
    for field, value in values.items():
        setattr(member, field, value)
    diff = audit.changes(member, fields, before)
    if diff:
        try:
            db.session.flush()
        except IntegrityError:  # another request took the address meanwhile (unique index)
            raise EmailInUse(EMAIL_IN_USE) from None
        audit.record(action, member, actor=actor, diff=diff)
    return diff


def update_member(member, data, *, actor=None):
    """Change a member's data (members.edit_any). Returns {field: [old, new]}. Commits nothing.

    Raises EmailInUse when the contact email belongs to someone else.
    """
    return _update(member, data, ADMIN_FIELDS, 'members.update', actor)


def update_own_profile(member, data, *, actor=None):
    """A member changes their own Biername, phone, address and what others may see.

    Names and email are not editable here (members.edit_any). Commits nothing.
    """
    return _update(member, data, OWN_FIELDS, 'members.profile.update', actor)
