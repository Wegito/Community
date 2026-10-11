from flask import flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from community.core.platform import audit
from community.core.security import admin_required
from community.extensions import db

from . import bp
from .forms import AnnouncementForm
from .models import Announcement


@bp.route('/')
@login_required
def index():
    anns = db.session.scalars(select(Announcement).order_by(Announcement.created_at.desc()).limit(10)).all()
    return render_template('announcements/index.html', anns=anns)


@bp.route('/announcements', endpoint='list')
@login_required
def list_announcements():
    anns = db.session.scalars(select(Announcement).order_by(Announcement.created_at.desc())).all()
    return render_template('announcements/list.html', anns=anns)


@bp.route('/announcements/new', methods=['GET', 'POST'])
@admin_required
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
