from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from community.core.authz import can, require_perm
from community.core.authz.policies import can_see_former_members
from community.core.identity.models import Member
from community.core.platform import audit
from community.core.platform.notify import notify
from community.core.time import today_local
from community.extensions import db

from . import services
from .forms import (
    ButtonForm,
    FirstRankForm,
    GroupAddForm,
    GroupForm,
    OfficeAssignForm,
    OfficeDescriptionForm,
    OfficeEndForm,
    RankChangeForm,
    RankLabelForm,
    SemesterForm,
    SemesterStatusForm,
)
from .models import GroupKind, GroupMembership, MemberGroup, OfficeAssignment, Rank, Semester, SemesterStatus

bp = Blueprint('org', __name__, url_prefix='/org', template_folder='templates')


def member_or_404(public_id):
    """The member behind the URL; former members only for can_see_former_members() (decisions 23, 32)."""
    member = db.session.scalar(select(Member).where(Member.public_id == public_id))
    if member is None or not (member.is_active_member or can_see_former_members()):
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
        form.valid_from.data = services.first_free_rank_day(member)
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
            notify(member, 'rank_changed', f'Du bist ab {form.valid_from.data:%d.%m.%Y} {rank.label}.')
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
        today=today_local(),
    )


@bp.route('/ranks/first', methods=['GET', 'POST'])
@require_perm('members.import')
def first_ranks():
    """Ohne Rang: members who never had a rank get their first one (after the update from M0, or an import)."""
    members = services.members_without_rank()
    ranks = {rank.id: rank for rank in services.ranks()}
    form = FirstRankForm()
    if request.method == 'GET':
        form.valid_from.data = today_local()
    if form.validate_on_submit():
        chosen = []
        for member in members:
            rank = ranks.get(request.form.get(f'rank-{member.id}', type=int))
            if rank is not None and member.id != current_user.member_id:  # the page offers no own rank
                chosen.append((member, rank))
        if not chosen:
            flash('Bitte mindestens einen Rang auswählen.', 'warning')
        else:
            try:
                for member, rank in chosen:
                    services.set_first_rank(member, rank, form.valid_from.data, changed_by=current_user)
            except services.OrgError as error:
                db.session.rollback()
                flash(f'{member.full_name}: {error}', 'danger')
            else:
                db.session.commit()
                flash(f'Rang gesetzt für {", ".join(member.full_name for member, _ in chosen)}', 'success')
                return redirect(url_for('org.first_ranks'))
    return render_template(
        'org/first_ranks.html', form=form, members=members, ranks=ranks.values(), chosen=request.form
    )


def _office_options():
    """How to treat Ämter the new rank may not hold: end them, or keep them as acting."""
    choice = request.form.get('offices', 'close')
    return {'offices': choice if choice in ('close', 'keep_acting') else 'close'}


def _office_conflicts(member):
    return services.member_offices_from(member)


def active_member_choices():
    from community.core.identity.models import MemberStatus

    members = db.session.scalars(
        select(Member).where(Member.status == MemberStatus.ACTIVE).order_by(Member.first_name, Member.last_name)
    ).all()
    return [(m.id, m.display_name) for m in members]


def office_or_404(key):
    office = services.office_by_key(key)
    if office is None:
        abort(404)
    return office


@bp.route('/offices')
@require_perm('members.directory.view')
def offices():
    rows = [(office, services.office_holders(office)) for office in services.offices()]
    return render_template('org/offices.html', rows=rows)


@bp.route('/offices/<key>', methods=['GET', 'POST'])
@require_perm('members.directory.view')
def office(key):
    office = office_or_404(key)
    form = OfficeDescriptionForm(obj=office)
    if request.method == 'POST':
        if not can('offices.assign'):
            abort(403)
        if form.validate_on_submit():
            office.description_md = form.description_md.data
            audit.record('org.office.describe', office)
            db.session.commit()
            flash('Gespeichert', 'success')
            return redirect(url_for('org.office', key=key))
    return render_template(
        'org/office.html',
        office=office,
        holders=services.office_holders_from(office),
        history=services.office_history(office),
        form=form,
        end_form=OfficeEndForm(),
        today=today_local(),
    )


@bp.route('/offices/<key>/assign', methods=['GET', 'POST'])
@require_perm('offices.assign')
def assign_office(key):
    office = office_or_404(key)
    form = OfficeAssignForm()
    form.member_id.choices = active_member_choices()
    semesters = db.session.scalars(select(Semester).order_by(Semester.starts_on.desc())).all()
    form.semester_id.choices = [(None, '—')] + [(s.id, s.code) for s in semesters]
    if request.method == 'GET':
        form.valid_from.data = today_local()
        current = services.current_semester()
        form.semester_id.data = current.id if current else None
    if form.validate_on_submit():
        member = db.get_or_404(Member, form.member_id.data)
        semester = db.session.get(Semester, form.semester_id.data) if form.semester_id.data else None
        try:
            assignment = services.assign_office(
                office,
                member,
                form.valid_from.data,
                assigned_by=current_user,
                is_acting=form.is_acting.data,
                semester=semester,
                elected_on=form.elected_on.data,
                convent_ref=form.convent_ref.data,
                end_previous=form.end_previous.data,
            )
        except services.OrgError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            _notify_office_holder(assignment)
            db.session.commit()
            flash(f'{member.full_name} ist ab {form.valid_from.data:%d.%m.%Y} {office.label}.', 'success')
            return redirect(url_for('org.office', key=key))
    return render_template('org/office_assign.html', office=office, form=form, holders=services.office_holders(office))


def _notify_office_holder(assignment):
    office = assignment.office
    acting = ' (kommissarisch)' if assignment.is_acting else ''
    notify(
        assignment.member,
        'office_assigned',
        f'Du bist ab {assignment.valid_from:%d.%m.%Y} {office.label}{acting}.',
        link=url_for('org.office', key=office.key),
        mail_template='office_assigned',
        mail_context={'office_label': office.label, 'description': office.description_md},
    )


@bp.route('/offices/assignments/<int:assignment_id>/end', methods=['POST'])
@require_perm('offices.assign')
def end_office(assignment_id):
    assignment = db.get_or_404(OfficeAssignment, assignment_id)
    form = OfficeEndForm()
    if form.validate_on_submit():
        try:
            services.end_assignment(assignment, form.valid_to.data, reason=form.reason.data, ended_by=current_user)
        except services.OrgError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash('Amtszeit beendet', 'success')
    else:
        flash('Bitte ein Datum angeben.', 'danger')
    return redirect(url_for('org.office', key=assignment.office.key))


@bp.route('/offices/assignments/<int:assignment_id>/cancel', methods=['POST'])
@require_perm('offices.assign')
def cancel_office(assignment_id):
    assignment = db.get_or_404(OfficeAssignment, assignment_id)
    office_key, name = assignment.office.key, assignment.member.full_name
    if ButtonForm().validate_on_submit():
        try:
            services.cancel_assignment(assignment, cancelled_by=current_user)
        except services.OrgError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash(f'{name} ausgetragen', 'success')
    return redirect(url_for('org.office', key=office_key))


@bp.route('/groups', methods=['GET', 'POST'])
@require_perm('groups.manage')
def groups():
    form = GroupForm()
    if form.validate_on_submit():
        try:
            group = services.create_group(form.name.data, form.kind.data, created_by=current_user)
        except services.OrgError as error:
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash('Gruppe angelegt', 'success')
            return redirect(url_for('org.group', group_id=group.id))
    rows = [(g, len(services.group_members(g))) for g in services.groups()]
    return render_template('org/groups.html', rows=rows, form=form, kind_labels=GroupKind.LABELS)


@bp.route('/groups/<int:group_id>', methods=['GET', 'POST'])
@require_perm('groups.manage')
def group(group_id):
    group = db.get_or_404(MemberGroup, group_id)
    form = GroupAddForm()
    form.member_id.choices = active_member_choices()
    if request.method == 'GET':
        form.valid_from.data = today_local()
    if form.validate_on_submit():
        member = db.get_or_404(Member, form.member_id.data)
        try:
            services.add_to_group(group, member, form.valid_from.data, is_lead=form.is_lead.data, added_by=current_user)
        except services.OrgError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash(f'{member.full_name} hinzugefügt', 'success')
            return redirect(url_for('org.group', group_id=group.id))
    return render_template(
        'org/group.html',
        group=group,
        memberships=services.group_members_from(group),
        today=today_local(),
        form=form,
        button_form=ButtonForm(),
        kind_labels=GroupKind.LABELS,
    )


@bp.route('/groups/memberships/<int:membership_id>/end', methods=['POST'])
@require_perm('groups.manage')
def end_membership(membership_id):
    membership = db.get_or_404(GroupMembership, membership_id)
    group_id, name, group_name = membership.group_id, membership.member.full_name, membership.group.name
    if ButtonForm().validate_on_submit():
        services.end_membership(membership, ended_by=current_user)
        db.session.commit()
        flash(f'{name} aus {group_name} entfernt', 'success')
    return redirect(url_for('org.group', group_id=group_id))


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
