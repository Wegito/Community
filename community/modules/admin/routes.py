from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, select
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional

from community.core.authz import require_perm
from community.core.club import NAME_MAX, SHORT_NAME_MAX, club_name, saved_club, update_club
from community.core.forms import FlaskForm
from community.core.platform import jobs as job_queue
from community.core.platform.audit import AuditLog
from community.extensions import db

from . import bp

PAGE_SIZE = 50


@bp.route('/audit')
@require_perm('audit.view')
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


class ButtonForm(FlaskForm):
    """CSRF-only form for single-button actions."""


@bp.route('/jobs')
@require_perm('system.admin')
def jobs():
    status = request.args.get('status', '')
    if status not in job_queue.JobStatus.ALL:
        status = ''
    return render_template(
        'admin/jobs.html',
        items=job_queue.recent_jobs(status or None),
        status=status,
        labels=job_queue.JobStatus.LABELS,
        form=ButtonForm(),
    )


@bp.route('/jobs/<int:job_id>/retry', methods=['POST'])
@require_perm('system.admin')
def retry_job(job_id):
    job = db.get_or_404(job_queue.Job, job_id)
    if ButtonForm().validate_on_submit():
        try:
            job_queue.retry_job(job, actor=current_user)
        except job_queue.JobError as error:
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash(f'Job {job.id} wird erneut versucht.', 'success')
    return redirect(url_for('admin.jobs'))


class ClubForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=NAME_MAX)])
    short_name = StringField('Kurzname (optional)', validators=[Optional(), Length(max=SHORT_NAME_MAX)])
    submit = SubmitField('Speichern')


@bp.route('/verein', methods=['GET', 'POST'])
@require_perm('system.admin')
def club():
    saved = saved_club()
    form = ClubForm(data={'name': club_name(), 'short_name': saved.short_name if saved else ''})
    if form.validate_on_submit():
        diff = update_club(form.name.data, form.short_name.data, actor=current_user)
        db.session.commit()
        flash('Gespeichert' if diff else 'Keine Änderungen', 'success')
        return redirect(url_for('admin.club'))
    return render_template('admin/club.html', form=form, from_env=saved is None)
