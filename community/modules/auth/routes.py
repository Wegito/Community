from datetime import timedelta

from flask import current_app, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from community.core.authz import public, require_login
from community.core.identity.models import MIN_PASSWORD_LENGTH, User
from community.core.identity.services import (
    EmailInUse,
    InvitationError,
    find_user_by_login_email,
    find_valid_invitation,
)
from community.core.platform import audit
from community.core.security import safe_next
from community.core.time import utcnow
from community.extensions import db

from . import bp, services
from .forms import (
    ChangeEmailForm,
    ChangePasswordForm,
    ConfirmForm,
    ForgotPasswordForm,
    JoinForm,
    LoginForm,
    NewPasswordForm,
)

LOGIN_FAILED = 'Login fehlgeschlagen. Nach mehreren Fehlversuchen wird der Login für kurze Zeit gesperrt.'
INVALID_LINK = services.INVALID_LINK
LOGIN_EMAIL_IN_USE = 'Diese E-Mail-Adresse gehört schon zu einem anderen Konto. Bitte eine andere angeben.'

# Checked when the email is unknown, so both cases take about the same time.
_DUMMY_HASH = generate_password_hash('timing-equaliser')


def _private(body, status=200):
    """The page's URL holds the invitation token: keep it out of caches and Referer headers."""
    response = make_response(body, status)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@bp.route('/join/<token>', methods=['GET', 'POST'])
@public
def join(token):
    """Accept a personal invitation: set the login email and password, then log in."""
    if current_user.is_authenticated:
        return _private(render_template('auth/join_logged_in.html'))
    invitation = find_valid_invitation(token)
    if invitation is None:
        # Unknown, expired, revoked and used links all look the same.
        return _private(render_template('auth/join_invalid.html', message=INVALID_LINK), 404)
    form = JoinForm()
    if request.method == 'GET':
        form.login_email.data = invitation.email or invitation.member.email
    if form.validate_on_submit():
        try:
            user = services.join(invitation, form.login_email.data, form.password.data)
        except EmailInUse:
            db.session.rollback()
            form.login_email.errors.append(LOGIN_EMAIL_IN_USE)
        except InvitationError:
            db.session.rollback()
            return _private(render_template('auth/join_invalid.html', message=INVALID_LINK), 404)
        else:
            db.session.commit()
            login_user(user)
            flash(f'Willkommen, {user.member.first_name}! Dein Konto ist angelegt und du bist angemeldet.', 'success')
            return redirect(url_for('members.profile'))
    return _private(
        render_template(
            'auth/join.html',
            form=form,
            invitation=invitation,
            member=invitation.member,
            min_password_length=MIN_PASSWORD_LENGTH,
        )
    )


@bp.route('/login', methods=['GET', 'POST'])
@public
def login():
    next_url = safe_next(request.args.get('next'))
    if current_user.is_authenticated:
        return redirect(next_url or url_for('announcements.index'))
    form = LoginForm()
    if form.validate_on_submit():
        now = utcnow()
        user = find_user_by_login_email(form.email.data)
        reason = None
        if user is None:
            check_password_hash(_DUMMY_HASH, form.password.data)
            reason = 'unknown_email'
        elif not User.reserve_login_attempt(
            user.id,
            now,
            max_attempts=current_app.config['MAX_FAILED_LOGINS'],
            lockout=timedelta(minutes=current_app.config['LOGIN_LOCKOUT_MINUTES']),
        ):
            # Locked: same message and same cost as a wrong password, so the response
            # does not reveal that the account exists. The lock expires on its own.
            check_password_hash(_DUMMY_HASH, form.password.data)
            reason = 'locked'
        elif not user.check_password(form.password.data):
            reason = 'wrong_password'
        elif not user.is_active:
            reason = 'disabled'
        else:
            User.record_successful_login(user.id, now)
            login_user(user, remember=form.remember.data)
            audit.record('auth.login', user, actor=user)
            db.session.commit()
            return redirect(next_url or url_for('announcements.index'))
        audit.record(
            'auth.login_failed',
            entity_type='user',
            entity_id=user.id if user else None,
            diff={'reason': reason, **({} if user else {'email': form.email.data})},
        )
        db.session.commit()
        flash(LOGIN_FAILED, 'danger')
    return render_template('auth/login.html', form=form)


@bp.route('/logout', methods=['POST'])
@require_login
def logout():
    audit.record('auth.logout', current_user._get_current_object())
    db.session.commit()
    logout_user()
    flash('Abgemeldet.', 'success')
    return redirect(url_for('auth.login'))


# --- Password reset, own password and login email (M1-8) ------------------------


def _link_invalid(kind, message=INVALID_LINK, status=404):
    """One page for every unusable reset or confirmation link; it does not say why."""
    return _private(render_template('auth/link_invalid.html', kind=kind, message=message), status)


def _has_remember_cookie():
    return current_app.config.get('REMEMBER_COOKIE_NAME', 'remember_token') in request.cookies


@bp.route('/password/forgot', methods=['GET', 'POST'])
@public
def forgot_password():
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        services.request_password_reset(form.email.data)
        try:
            db.session.commit()
        except IntegrityError:  # the same request twice at once: the other one queued the mail
            db.session.rollback()
        # The same page whether or not the address has an account.
        return render_template(
            'auth/forgot_password_sent.html', minutes=current_app.config['PASSWORD_RESET_MAX_AGE_MINUTES']
        )
    return render_template('auth/forgot_password.html', form=form)


@bp.route('/password/reset/<token>', methods=['GET', 'POST'])
@public
def reset_password(token):
    user = services.user_from_reset_token(token)
    if user is None:
        return _link_invalid('reset')
    form = NewPasswordForm()
    if form.validate_on_submit():
        try:
            services.reset_password(token, form.password.data)
        except services.InvalidLink:
            db.session.rollback()
            return _link_invalid('reset')
        db.session.commit()
        if current_user.is_authenticated:
            logout_user()
        flash(
            'Dein Passwort ist geändert, alle Anmeldungen sind beendet. Bitte mit dem neuen Passwort einloggen.',
            'success',
        )
        return redirect(url_for('auth.login'))
    return _private(
        render_template('auth/reset_password.html', form=form, user=user, min_password_length=MIN_PASSWORD_LENGTH)
    )


@bp.route('/settings/password', methods=['GET', 'POST'])
@require_login
def change_password():
    user = current_user._get_current_object()
    form = ChangePasswordForm()
    if form.validate_on_submit():
        try:
            services.change_password(user, form.current_password.data, form.password.data)
        except services.WrongPassword as error:
            db.session.rollback()
            form.current_password.errors.append(str(error))
        else:
            db.session.commit()
            # The new session_version ends every other session. This browser logs in again,
            # with a new "Angemeldet bleiben" cookie if it had one.
            login_user(user, remember=_has_remember_cookie())
            flash('Passwort geändert. Auf allen anderen Geräten bist du jetzt abgemeldet.', 'success')
            return redirect(url_for('members.profile'))
    return render_template('auth/change_password.html', form=form, min_password_length=MIN_PASSWORD_LENGTH)


@bp.route('/settings/email', methods=['GET', 'POST'])
@require_login
def change_email():
    user = current_user._get_current_object()
    form = ChangeEmailForm()
    if form.validate_on_submit():
        new_email = form.login_email.data
        try:
            services.request_email_change(user, new_email, form.password.data)
            db.session.commit()
        except services.WrongPassword as error:
            db.session.rollback()
            form.password.errors.append(str(error))
        except services.AccountError as error:
            db.session.rollback()
            form.login_email.errors.append(str(error))
        except IntegrityError:  # sent twice at once: the other request queued the mail
            db.session.rollback()
        if not (form.password.errors or form.login_email.errors):
            flash(
                f'Wir haben einen Bestätigungslink an {new_email} geschickt. '
                'Bis du die neue Adresse dort bestätigst, bleibt deine bisherige Login-E-Mail-Adresse gültig.',
                'success',
            )
            return redirect(url_for('members.profile'))
    return render_template('auth/change_email.html', form=form)


@bp.route('/settings/email/confirm/<token>', methods=['GET', 'POST'])
@public
def confirm_email(token):
    """Confirm a new login email. GET only shows the button: mail scanners that open links change nothing."""
    found = services.email_change_from_token(token)
    if found is None:
        return _link_invalid('email')
    user, new_email = found
    form = ConfirmForm()
    if form.validate_on_submit():
        try:
            services.confirm_email_change(token)
        except services.InvalidLink:
            db.session.rollback()
            return _link_invalid('email')
        except services.LoginEmailInUse as error:
            db.session.rollback()
            return _link_invalid('email', message=str(error), status=409)
        db.session.commit()
        flash(f'Deine Login-E-Mail-Adresse ist jetzt {new_email}.', 'success')
        if current_user.is_authenticated and current_user.id == user.id:
            return redirect(url_for('members.profile'))
        return redirect(url_for('auth.login'))
    return _private(render_template('auth/confirm_email.html', form=form, user=user, new_email=new_email))
