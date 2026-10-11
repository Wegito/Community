from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from community.core.authz import can, current_permissions, require_login, require_perm
from community.core.org import services as org
from community.extensions import db

from . import bp, services
from .forms import MemberForm, ProfileForm


def member_or_404(public_id):
    """The member behind the URL. Former members exist only for members.edit_any holders."""
    member = services.member_by_public_id(public_id)
    if member is None or not (member.is_active_member or can('members.edit_any')):
        abort(404)
    return member


@bp.route('/members')
@require_perm('members.directory.view')
def directory():
    ranks = org.ranks()
    q = request.args.get('q', '').strip()
    rank_key = request.args.get('rank', '')
    if rank_key not in {r.key for r in ranks}:
        rank_key = ''
    include_ended = request.args.get('ended') == '1' and can('members.edit_any')
    rows = services.directory(q=q, rank_key=rank_key or None, include_ended=include_ended)
    return render_template(
        'members/directory.html', rows=rows, ranks=ranks, q=q, rank_key=rank_key, include_ended=include_ended
    )


@bp.route('/m/<public_id>')
@require_perm('members.directory.view')
def detail(public_id):
    member = member_or_404(public_id)
    is_self = member.id == current_user.member_id
    return render_template(
        'members/detail.html',
        member=member,
        overview=services.overview(member),
        contact=services.visible_contact(member, current_permissions(), is_self=is_self),
        is_self=is_self,
    )


@bp.route('/m/<public_id>/edit', methods=['GET', 'POST'])
@require_perm('members.edit_any')
def edit(public_id):
    member = member_or_404(public_id)
    form = MemberForm(obj=member)
    if form.validate_on_submit():
        data = {field: getattr(form, field).data for field in services.ADMIN_FIELDS}
        try:
            diff = services.update_member(member, data, actor=current_user)
        except services.EmailInUse as error:
            db.session.rollback()
            form.email.errors.append(str(error))
        except services.MemberError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash('Gespeichert' if diff else 'Keine Änderungen', 'success')
            return redirect(url_for('members.detail', public_id=member.public_id))
    return render_template('members/edit.html', member=member, form=form)


@bp.route('/profile', methods=['GET', 'POST'])
@require_login
def profile():
    member = current_user.member
    form = ProfileForm(obj=member)
    if form.validate_on_submit():
        data = {field: getattr(form, field).data for field in services.OWN_FIELDS}
        diff = services.update_own_profile(member, data, actor=current_user)
        db.session.commit()
        flash('Gespeichert' if diff else 'Keine Änderungen', 'success')
        return redirect(url_for('members.profile'))
    return render_template(
        'members/profile.html',
        member=member,
        overview=services.overview(member),
        contact_email=services.contact_email(member),
        form=form,
    )
