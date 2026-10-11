from flask import abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from community.core.identity.models import User
from community.core.security import admin_required
from community.extensions import db

from . import bp
from .forms import ChoreForm, ToggleDoneForm
from .models import Chore


def _assignee_choices():
    users = db.session.scalars(select(User).order_by(User.name)).all()
    return [(None, '— niemand —')] + [(u.id, u.name) for u in users]


@bp.route('/chores', endpoint='list')
@login_required
def list_chores():
    items = db.session.scalars(select(Chore).order_by(Chore.due_date.is_(None), Chore.due_date, Chore.id)).all()
    return render_template('tasks/list.html', items=items, toggle_form=ToggleDoneForm())


@bp.route('/chores/new', methods=['GET', 'POST'])
@admin_required
def create():
    form = ChoreForm()
    form.assigned_to_id.choices = _assignee_choices()
    if form.validate_on_submit():
        item = Chore()
        form.populate_obj(item)
        db.session.add(item)
        db.session.commit()
        flash('Aufgabe angelegt', 'success')
        return redirect(url_for('tasks.list'))
    return render_template('tasks/edit.html', form=form, item=None)


@bp.route('/chores/edit/<int:cid>', methods=['GET', 'POST'])
@login_required
def edit(cid):
    item = db.get_or_404(Chore, cid)
    if not item.can_edit(current_user):
        abort(403)
    form = ChoreForm(obj=item)
    form.assigned_to_id.choices = _assignee_choices()
    if form.validate_on_submit():
        form.populate_obj(item)
        db.session.commit()
        flash('Gespeichert', 'success')
        return redirect(url_for('tasks.list'))
    return render_template('tasks/edit.html', form=form, item=item)


@bp.route('/chores/<int:cid>/toggle', methods=['POST'])
@login_required
def toggle_done(cid):
    item = db.get_or_404(Chore, cid)
    if not item.can_toggle_done(current_user):
        abort(403)
    if ToggleDoneForm().validate_on_submit():
        item.is_done = not item.is_done
        db.session.commit()
        flash('Als erledigt markiert' if item.is_done else 'Wieder offen', 'success')
    return redirect(url_for('tasks.list'))
