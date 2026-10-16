"""Start page card for the technical admin: background jobs and logins."""

from datetime import timedelta

from sqlalchemy import func, select

from community.core.identity.models import Member, MemberStatus, User
from community.core.platform.jobs import Job, JobStatus
from community.core.time import utcnow
from community.core.widgets import OFFICE, home_card
from community.extensions import db


@home_card('admin/_home_system.html', permission='system.admin', order=90, area=OFFICE)
def system_card(member):
    def count(model, *conditions):
        return db.session.scalar(select(func.count()).select_from(model).where(*conditions))

    week_ago = utcnow() - timedelta(days=7)
    return {
        'failed_jobs': count(Job, Job.status == JobStatus.FAILED),
        'pending_jobs': count(Job, Job.status == JobStatus.PENDING),
        'logins': count(User, User.is_enabled.is_(True)),
        'active_week': count(User, User.last_login_at >= week_ago),
        'members': count(Member, Member.status == MemberStatus.ACTIVE),
    }
