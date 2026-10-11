from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, logout_user

from community.core.authz import can, current_permissions, require_login, require_perm
from community.core.org import services as org
from community.core.time import today_local
from community.extensions import db

from . import bp, services
from .forms import ExitForm, MemberForm, ProfileForm


def can_see_former_members():
    """Former members are listed and shown only to those who manage member data or exits."""
    return can('members.edit_any') or can('members.exit.record')


def member_or_404(public_id):
    """The member behind the URL. Former members exist only for can_see_former_members()."""
    member = services.member_by_public_id(public_id)
    if member is None or not (member.is_active_member or can_see_former_members()):
        abort(404)
    return member


def login_of_or_404(member):
    """The member's login account; members without a login have nothing to lock or log out."""
    if member.user is None:
        abort(404)
    return member.user


@bp.route('/members')
@require_perm('members.directory.view')
def directory():
    ranks = org.ranks()
    q = request.args.get('q', '').strip()
    rank_key = request.args.get('rank', '')
    if rank_key not in {r.key for r in ranks}:
        rank_key = ''
    include_ended = request.args.get('ended') == '1' and can_see_former_members()
    rows = services.directory(q=q, rank_key=rank_key or None, include_ended=include_ended)
    return render_template(
        'members/directory.html',
        rows=rows,
        ranks=ranks,
        q=q,
        rank_key=rank_key,
        include_ended=include_ended,
        can_see_former=can_see_former_members(),
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


# --- Lifecycle -------------------------------------------------------------------


def _back_to(member):
    return redirect(url_for('members.detail', public_id=member.public_id))


def _apply(change, success):
    """Run a service call: commit and flash `success`, or roll back and flash the rule broken."""
    try:
        result = change()
    except services.MemberError as error:
        db.session.rollback()
        flash(str(error), 'danger')
        return None
    db.session.commit()
    flash(success, 'success')
    return result


@bp.route('/m/<public_id>/login/disable', methods=['POST'])
@require_perm('members.edit_any')
def disable_login(public_id):
    member = member_or_404(public_id)
    login_of_or_404(member)
    _apply(
        lambda: services.disable_login(member, actor=current_user),
        f'Login von {member.full_name} gesperrt, alle Sitzungen beendet.',
    )
    return _back_to(member)


@bp.route('/m/<public_id>/login/enable', methods=['POST'])
@require_perm('members.edit_any')
def enable_login(public_id):
    member = member_or_404(public_id)
    login_of_or_404(member)
    _apply(lambda: services.enable_login(member, actor=current_user), f'Login von {member.full_name} entsperrt.')
    return _back_to(member)


def _logout_self():
    user = current_user._get_current_object()
    services.logout_everywhere(user, actor=user)
    db.session.commit()
    logout_user()
    flash('Auf allen Geräten abgemeldet. Bitte neu einloggen.', 'success')
    return redirect(url_for('auth.login'))


@bp.route('/profile/logout-everywhere', methods=['POST'])
@require_login
def logout_everywhere_own():
    return _logout_self()


@bp.route('/m/<public_id>/logout-everywhere', methods=['POST'])
@require_perm('members.edit_any')
def logout_everywhere(public_id):
    member = member_or_404(public_id)
    user = login_of_or_404(member)
    if user.id == current_user.id:
        return _logout_self()
    _apply(
        lambda: services.logout_everywhere(user, actor=current_user),
        f'{member.full_name} ist auf allen Geräten abgemeldet.',
    )
    return _back_to(member)


@bp.route('/m/<public_id>/exit', methods=['GET', 'POST'])
@require_perm('members.exit.record')
def record_exit(public_id):
    member = member_or_404(public_id)
    if not member.is_active_member:
        flash(f'{member.full_name} ist bereits ausgeschieden.', 'warning')
        return _back_to(member)
    form = ExitForm()
    if request.method == 'GET':
        form.ended_on.data = today_local()
    if form.validate_on_submit():
        notified = _apply(
            lambda: services.exit_member(
                member, form.ended_on.data, form.reason.data, recorded_by=current_user, note=form.note.data
            ),
            f'Austritt von {member.full_name} zum {form.ended_on.data:%d.%m.%Y} eingetragen.',
        )
        if notified is not None:
            if not notified:
                flash('Kein Kassenwart eingetragen: bitte die Abrechnung des Saldos selbst veranlassen.', 'warning')
            return _back_to(member)
    return render_template(
        'members/exit.html',
        member=member,
        form=form,
        overview=services.overview(member),
        is_self=member.id == current_user.member_id,
    )


@bp.route('/m/<public_id>/reactivate', methods=['POST'])
@require_perm('members.exit.record')
def reactivate(public_id):
    member = member_or_404(public_id)
    if _apply(
        lambda: services.reactivate_member(member, actor=current_user),
        f'{member.full_name} ist wieder aktives Mitglied.',
    ):
        flash('Rang, Ämter und Gruppen wurden nicht wiederhergestellt. Bitte den Rang neu setzen.', 'warning')
    return _back_to(member)
