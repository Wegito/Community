from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from community.core.authz import require_perm
from community.core.identity.models import Member
from community.core.platform import audit
from community.core.time import today_local
from community.extensions import db

from . import services
from .forms import RankChangeForm, RankLabelForm, SemesterForm, SemesterStatusForm
from .models import Rank, Semester, SemesterStatus

bp = Blueprint('org', __name__, url_prefix='/org', template_folder='templates')


def member_or_404(public_id):
    member = db.session.scalar(select(Member).where(Member.public_id == public_id))
    if member is None:
        abort(404)
    return member


@bp.route('/semesters', methods=['GET', 'POST'])
@require_perm('org.semesters.manage')
def semesters():
    form = SemesterForm()
    if request.method == 'GET':
        suggestion = services.suggest_next_semester()
        form.code.data, form.kind.data = suggestion['code'], suggestion['kind']
        form.starts_on.data, form.ends_on.data = suggestion['starts_on'], suggestion['ends_on']
    if form.validate_on_submit():
        try:
            services.create_semester(form.code.data, form.kind.data, form.starts_on.data, form.ends_on.data)
        except services.OrgError as error:
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash('Semester angelegt', 'success')
            return redirect(url_for('org.semesters'))
    items = db.session.scalars(select(Semester).order_by(Semester.starts_on.desc())).all()
    return render_template(
        'org/semesters.html',
        form=form,
        items=items,
        current=services.current_semester(),
        status_form=SemesterStatusForm(),
        status_labels=SemesterStatus.LABELS,
    )


@bp.route('/semesters/<int:semester_id>/status', methods=['POST'])
@require_perm('org.semesters.manage')
def semester_status(semester_id):
    semester = db.get_or_404(Semester, semester_id)
    form = SemesterStatusForm()
    if form.validate_on_submit():
        old = semester.status
        semester.status = form.status.data
        audit.record('org.semester.status', semester, diff={'status': [old, semester.status]})
        db.session.commit()
    return redirect(url_for('org.semesters'))


@bp.route('/members/<public_id>/rank', methods=['GET', 'POST'])
@require_perm('members.rank.change')
def change_rank(public_id):
    member = member_or_404(public_id)
    form = RankChangeForm()
    form.rank_id.choices = [(r.id, r.label) for r in services.ranks()]
    if request.method == 'GET':
        form.valid_from.data = today_local()
    if form.validate_on_submit():
        rank = db.get_or_404(Rank, form.rank_id.data)
        try:
            services.change_rank(
                member,
                rank,
                form.valid_from.data,
                changed_by=current_user,
                convent_ref=form.convent_ref.data,
                note=form.note.data,
                **_office_options(),
            )
        except services.OrgError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash(f'{member.full_name} ist ab {form.valid_from.data:%d.%m.%Y} {rank.label}.', 'success')
            return redirect(url_for('org.change_rank', public_id=member.public_id))
    return render_template(
        'org/rank_change.html',
        member=member,
        form=form,
        current=services.current_rank(member),
        history=services.rank_history(member),
        own=member.id == current_user.member_id,
        office_conflicts=_office_conflicts(member),
    )


def _office_options():
    """How to treat Ämter the new rank may not hold (filled in by the offices code, M1-5)."""
    return {}


def _office_conflicts(member):
    return []


@bp.route('/ranks', methods=['GET', 'POST'])
@require_perm('system.admin')
def ranks():
    rows = services.ranks(include_archived=True)
    forms = {rank.id: RankLabelForm(prefix=f'r{rank.id}', obj=rank) for rank in rows}
    if request.method == 'POST':
        rank = db.get_or_404(Rank, request.form.get('rank_id', type=int))
        form = RankLabelForm(prefix=f'r{rank.id}')
        if form.validate_on_submit():
            before = audit.snapshot(rank, ('label', 'abbreviation'))
            form.populate_obj(rank)
            audit.record('org.rank.rename', rank, diff=audit.changes(rank, ('label', 'abbreviation'), before))
            db.session.commit()
            flash('Gespeichert', 'success')
            return redirect(url_for('org.ranks'))
        forms[rank.id] = form
    return render_template('org/ranks.html', rows=rows, forms=forms)
