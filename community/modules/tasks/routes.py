from flask import abort, flash, redirect, render_template, url_for
from flask_login import current_user
from sqlalchemy import select

from community.core.authz import can, require_perm
from community.core.authz.policies import can_complete_task
from community.core.identity.models import Member, User
from community.core.platform import audit
from community.extensions import db

from . import bp
from .forms import ChoreForm, ToggleDoneForm
from .models import Chore

AUDITED_FIELDS = ('title', 'assigned_to_id', 'due_date', 'is_done')


def _assignee_choices():
    users = db.session.scalars(select(User).join(User.member).order_by(Member.first_name, Member.last_name)).all()
    return [(None, '— niemand —')] + [(u.id, u.display_name) for u in users]


@bp.route('/chores', endpoint='list')
@require_perm('tasks.view_own')
def list_chores():
    query = select(Chore).order_by(Chore.due_date.is_(None), Chore.due_date, Chore.id)
    if not can('tasks.view_all'):
        query = query.where(Chore.assigned_to_id == current_user.id)
    items = db.session.scalars(query).all()
    return render_template('tasks/list.html', items=items, toggle_form=ToggleDoneForm())


@bp.route('/chores/new', methods=['GET', 'POST'])
@require_perm('tasks.manage')
def create():
    form = ChoreForm()
    form.assigned_to_id.choices = _assignee_choices()
    if form.validate_on_submit():
        item = Chore()
        form.populate_obj(item)
        db.session.add(item)
        db.session.flush()
        audit.record('chore.create', item, diff=audit.snapshot(item, AUDITED_FIELDS))
        db.session.commit()
        flash('Aufgabe angelegt', 'success')
        return redirect(url_for('tasks.list'))
    return render_template('tasks/edit.html', form=form, item=None)


@bp.route('/chores/edit/<int:cid>', methods=['GET', 'POST'])
@require_perm('tasks.manage')
def edit(cid):
    item = db.get_or_404(Chore, cid)
    form = ChoreForm(obj=item)
    form.assigned_to_id.choices = _assignee_choices()
    if form.validate_on_submit():
        before = audit.snapshot(item, AUDITED_FIELDS)
        form.populate_obj(item)
        audit.record('chore.update', item, diff=audit.changes(item, AUDITED_FIELDS, before))
        db.session.commit()
        flash('Gespeichert', 'success')
        return redirect(url_for('tasks.list'))
    return render_template('tasks/edit.html', form=form, item=item)


@bp.route('/chores/<int:cid>/toggle', methods=['POST'])
@require_perm('tasks.complete_own')
def toggle_done(cid):
    item = db.get_or_404(Chore, cid)
    if not can_complete_task(current_user, item):
        abort(403)
    if ToggleDoneForm().validate_on_submit():
        item.is_done = not item.is_done
        audit.record('chore.toggle_done', item, diff={'is_done': item.is_done})
        db.session.commit()
        flash('Als erledigt markiert' if item.is_done else 'Wieder offen', 'success')
    return redirect(url_for('tasks.list'))
