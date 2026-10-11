from flask import render_template, request
from sqlalchemy import func, select

from community.core.platform.audit import AuditLog
from community.core.security import admin_required
from community.extensions import db

from . import bp

PAGE_SIZE = 50


@bp.route('/audit')
@admin_required
def audit():
    action = request.args.get('action', '').strip()
    page = max(request.args.get('page', 1, type=int), 1)
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action.startswith(action, autoescape=True))
    total = db.session.scalar(select(func.count()).select_from(query.subquery()))
    entries = db.session.scalars(
        query.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc()).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)
    ).all()
    return render_template(
        'admin/audit.html',
        entries=entries,
        action=action,
        page=page,
        has_next=page * PAGE_SIZE < total,
        total=total,
    )
