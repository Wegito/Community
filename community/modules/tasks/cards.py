"""Start page cards of the Putzplan: my open tasks; open and overdue tasks for whoever plans them."""

from flask_login import current_user
from sqlalchemy import func, select

from community.core.time import today_local
from community.core.widgets import MAIN, OFFICE, home_card
from community.extensions import db

from .forms import ToggleDoneForm
from .models import Chore

MINE_LIMIT = 5


@home_card('tasks/_home_mine.html', permission='tasks.view_own', order=20, area=MAIN)
def my_tasks_card(member):
    query = (
        select(Chore)
        .where(Chore.assigned_to_id == current_user.id, Chore.is_done.is_not(True))
        .order_by(Chore.due_date.is_(None), Chore.due_date, Chore.id)
    )
    items = db.session.scalars(query.limit(MINE_LIMIT + 1)).all()
    if not items:
        return None
    return {
        'items': items[:MINE_LIMIT],
        'more': len(items) > MINE_LIMIT,
        'today': today_local(),
        'form': ToggleDoneForm(),
    }


@home_card('tasks/_home_manage.html', permission='tasks.manage', order=30, area=OFFICE)
def planning_card(member):
    today = today_local()
    open_tasks = Chore.is_done.is_not(True)

    def count(*conditions):
        return db.session.scalar(select(func.count()).select_from(Chore).where(open_tasks, *conditions))

    return {
        'open': count(),
        'overdue': count(Chore.due_date < today),
        'unassigned': count(Chore.assigned_to_id.is_(None)),
    }
