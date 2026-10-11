"""Joining with a personal invitation (roadmap §3.3)."""

from flask import current_app, url_for

from community.core.identity.services import accept_invitation
from community.core.org import services as org
from community.core.platform.notify import notify
from community.core.time import utcnow

# The Amt that looks after new members and is told when one joins.
NEW_MEMBER_OFFICE = 'fuxmajor'


def join(invitation, login_email, password):
    """An invited member creates their login (/join/<token>). Returns the User. Commits nothing.

    Accepts the invitation, welcomes the member and tells the Fuxmajor (bell and mail, as each
    of them chose). The caller logs the new user in after the commit.
    Raises InvitationError or EmailInUse (community.core.identity.services).
    """
    user = accept_invitation(invitation, login_email, password)
    user.last_login_at = utcnow()  # logged in right away
    member = user.member
    club = current_app.config['CLUB_NAME']
    notify(
        member,
        'welcome',
        f'Willkommen bei {club}, {member.first_name}! Ergänze in deinem Profil Biername und Telefon.',
        link=url_for('members.profile'),
    )
    for holder in new_member_officers(exclude=member):
        notify(
            holder,
            'new_member',
            f'{member.display_name} hat die Einladung angenommen und ist jetzt angemeldet.',
            link=url_for('members.detail', public_id=member.public_id),
        )
    return user


def new_member_officers(exclude=None):
    """Current Fuxmajor(s), as active members; `exclude` is left out (the new member)."""
    office = org.office_by_key(NEW_MEMBER_OFFICE)
    if office is None:
        return []
    return [
        a.member
        for a in org.office_holders(office)
        if a.member.is_active_member and (exclude is None or a.member_id != exclude.id)
    ]
