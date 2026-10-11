from flask import flash, redirect, render_template, url_for
from flask_login import current_user
from sqlalchemy import select

from community.core.authz import can, require_login, require_perm
from community.core.platform import audit
from community.extensions import db

from . import bp
from .forms import AnnouncementForm
from .models import Announcement


@bp.route('/')
@require_login
def index():
    anns = []
    if can('announcements.view'):
        anns = db.session.scalars(select(Announcement).order_by(Announcement.created_at.desc()).limit(10)).all()
    return render_template('announcements/index.html', anns=anns)


@bp.route('/announcements', endpoint='list')
@require_perm('announcements.view')
def list_announcements():
    anns = db.session.scalars(select(Announcement).order_by(Announcement.created_at.desc())).all()
    return render_template('announcements/list.html', anns=anns)


@bp.route('/announcements/new', methods=['GET', 'POST'])
@require_perm('announcements.create')
def create():
    form = AnnouncementForm()
    if form.validate_on_submit():
        a = Announcement(title=form.title.data, body=form.body.data, author_id=current_user.id)
        db.session.add(a)
        db.session.flush()
        audit.record('announcement.create', a, diff={'title': a.title})
        db.session.commit()
        flash('Ankündigung erstellt', 'success')
        return redirect(url_for('announcements.list'))
    return render_template('announcements/create.html', form=form)
