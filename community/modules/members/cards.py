"""Start page card for those who take in new members: open invitations, members without rank."""

from sqlalchemy import select

from community.core.identity.models import Invitation, Member, MemberStatus
from community.core.org import services as org
from community.core.time import utcnow
from community.core.widgets import OFFICE, home_card
from community.extensions import db


@home_card('members/_home_invitations.html', permission='members.invite', order=20, area=OFFICE)
def invitations_card(member):
    now = utcnow()
    open_invitations = db.session.scalars(
        select(Invitation)
        .join(Member, Member.id == Invitation.member_id)
        .where(
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
            Member.status == MemberStatus.ACTIVE,
        )
        .order_by(Invitation.expires_at)
    ).all()
    without_login = db.session.scalars(
        select(Member).where(Member.status == MemberStatus.ACTIVE, ~Member.user.has())
    ).all()
    return {
        'open': open_invitations,
        'without_login': len(without_login),
        'without_rank': len(org.members_without_rank()),
    }
