from flask import abort, flash, make_response, redirect, render_template, request, session, url_for
from flask_login import current_user, logout_user

from community.core.authz import can, current_permissions, require_login, require_perm
from community.core.identity.models import Invitation, InvitationChannel
from community.core.identity.services import open_invitations
from community.core.org import services as org
from community.core.platform.qr import qr_svg
from community.core.time import today_local
from community.extensions import db

from . import bp, services
from .forms import ButtonForm, ExitForm, InviteForm, MemberForm, NewMemberForm, ProfileForm

# The raw token of the newest link/QR invitation, so the result page survives a reload
# (only the hash is stored in the database). Bound to the inviter's user id.
INVITATION_SESSION_KEY = 'invitation_link'


def can_see_former_members():
    """Former members are listed and shown only to those who manage member data or exits."""
    return can('members.edit_any') or can('members.exit.record')


def member_or_404(public_id):
    """The member behind the URL. Former members exist only for can_see_former_members()."""
    member = services.member_by_public_id(public_id)
    if member is None or not (member.is_active_member or can_see_former_members()):
        abort(404)
    return member


def invitation_or_404(invitation_id):
    """An invitation, under the same visibility rule as its member's page."""
    invitation = db.get_or_404(Invitation, invitation_id)
    if not (invitation.member.is_active_member or can_see_former_members()):
        abort(404)
    return invitation


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
    can_invite = can('members.invite') and member.user is None and member.is_active_member
    return render_template(
        'members/detail.html',
        member=member,
        overview=services.overview(member),
        contact=services.visible_contact(member, current_permissions(), is_self=is_self),
        is_self=is_self,
        can_invite=can_invite,
        invitations=open_invitations(member) if can_invite else [],
        button_form=ButtonForm(),
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


# --- New members and invitations -------------------------------------------------


@bp.route('/members/new', methods=['GET', 'POST'])
@require_perm('members.invite')
def new():
    ranks = services.starting_ranks(current_permissions())
    form = NewMemberForm()
    form.rank_id.choices = [(r.id, r.label) for r in ranks]
    if request.method == 'GET':
        form.joined_on.data = today_local()
        form.rank_id.data = next((r.id for r in ranks if r.key == services.STARTING_RANK), None)
    if form.validate_on_submit():
        rank = next(r for r in ranks if r.id == form.rank_id.data)
        data = {field: getattr(form, field).data for field in ('first_name', 'last_name', 'biername', 'email')}
        data['joined_on'] = form.joined_on.data
        try:
            member, invitation, raw_token = services.add_member(
                data, rank, form.delivery.data, invited_by=current_user, permissions=current_permissions()
            )
        except services.EmailInUse as error:
            db.session.rollback()
            form.email.errors.append(str(error))
        except services.MemberError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            flash(f'{member.full_name} ist angelegt ({rank.label} ab {form.joined_on.data:%d.%m.%Y}).', 'success')
            if invitation is None:
                return redirect(url_for('members.detail', public_id=member.public_id))
            return _invitation_created(invitation, raw_token)
    return render_template('members/new.html', form=form, ranks=ranks)


@bp.route('/m/<public_id>/invite', methods=['GET', 'POST'])
@require_perm('members.invite')
def invite(public_id):
    member = member_or_404(public_id)
    form = InviteForm()
    invitable = member.user is None and member.is_active_member
    if request.method == 'GET':
        form.email.data = member.email
    if invitable and form.validate_on_submit():
        try:
            invitation, raw_token = services.invite(
                member, form.delivery.data, invited_by=current_user, email=form.email.data
            )
        except services.EmailInUse as error:
            db.session.rollback()
            form.email.errors.append(str(error))
        except services.MemberError as error:
            db.session.rollback()
            flash(str(error), 'danger')
        else:
            db.session.commit()
            return _invitation_created(invitation, raw_token)
    return render_template(
        'members/invite.html',
        member=member,
        form=form,
        invitable=invitable,
        invitations=open_invitations(member) if invitable else [],
        button_form=ButtonForm(),
    )


def _invitation_created(invitation, raw_token):
    """Post/redirect/get: the result page shows the link, so a reload does not invite twice.

    Links and QR codes are kept in the inviter's session to be shown there. Emailed links are
    not: only the recipient should have them.
    """
    if invitation.channel == InvitationChannel.EMAIL:
        session.pop(INVITATION_SESSION_KEY, None)
        flash(f'Die Einladung geht per E-Mail an {invitation.email}.', 'success')
    else:
        session[INVITATION_SESSION_KEY] = {'id': invitation.id, 'user_id': current_user.id, 'token': raw_token}
    return redirect(url_for('members.invited', invitation_id=invitation.id))


def _token_from_session(invitation):
    """The raw token of `invitation` if this inviter created it in this session and it is still open."""
    stored = session.get(INVITATION_SESSION_KEY) or {}
    if stored.get('id') != invitation.id or stored.get('user_id') != current_user.id:
        return None
    if not invitation.is_open():
        session.pop(INVITATION_SESSION_KEY, None)
        return None
    return stored.get('token')


@bp.route('/members/invited/<int:invitation_id>')
@require_perm('members.invite')
def invited(invitation_id):
    """Result page: the link or QR code (in the session that created it), otherwise the state."""
    invitation = invitation_or_404(invitation_id)
    raw_token = _token_from_session(invitation)
    link = services.join_url(raw_token) if raw_token else None
    qr = None
    if link and invitation.channel == InvitationChannel.QR:
        qr = qr_svg(link, title=f'Einladung für {invitation.member.full_name}')
    response = make_response(
        render_template(
            'members/invitation.html',
            invitation=invitation,
            member=invitation.member,
            link=link,
            qr=qr,
            button_form=ButtonForm(),
        )
    )
    # The page may contain a usable link: keep it out of caches and Referer headers.
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@bp.route('/invitations/<int:invitation_id>/revoke', methods=['POST'])
@require_perm('members.invite')
def revoke_invitation(invitation_id):
    invitation = invitation_or_404(invitation_id)
    if ButtonForm().validate_on_submit():
        _apply(lambda: services.revoke_invitation(invitation, actor=current_user), 'Einladung widerrufen.')
    return redirect(url_for('members.detail', public_id=invitation.member.public_id))


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
