from flask import flash, redirect, render_template, url_for
from flask_login import login_required
from sqlalchemy import select

from community.extensions import db

from . import bp
from .forms import ChoreForm
from .models import Chore


@bp.route('/chores', endpoint='list')
@login_required
def list_chores():
    items = db.session.scalars(select(Chore).order_by(Chore.due_date.is_(None), Chore.due_date, Chore.id)).all()
    return render_template('tasks/list.html', items=items)


@bp.route('/chores/edit/<int:cid>', methods=['GET', 'POST'])
@login_required
def edit(cid):
    item = db.session.get(Chore, cid)
    form = ChoreForm(obj=item)
    if form.validate_on_submit():
        form.populate_obj(item)
        db.session.commit()
        flash('Gespeichert', 'success')
        return redirect(url_for('tasks.list'))
    return render_template('tasks/edit.html', form=form, item=item)
