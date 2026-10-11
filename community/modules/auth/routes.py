import hmac
from datetime import timedelta

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from community.core.identity.models import User, split_name
from community.core.identity.services import create_member, create_user, find_user_by_login_email
from community.core.security import safe_next
from community.core.time import utcnow
from community.extensions import db

from . import bp
from .forms import LoginForm, RegisterForm

LOGIN_FAILED = 'Login fehlgeschlagen. Nach mehreren Fehlversuchen wird der Login für kurze Zeit gesperrt.'

# Checked when the email is unknown, so both cases take about the same time.
_DUMMY_HASH = generate_password_hash('timing-equaliser')


@bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('announcements.index'))
    form = RegisterForm()
    if form.validate_on_submit():
        invite_code = current_app.config.get('INVITE_CODE')
        if not invite_code or not hmac.compare_digest(form.invite.data.encode(), invite_code.encode()):
            flash('Falscher Invite-Code', 'danger')
            return render_template('auth/register.html', form=form)
        if find_user_by_login_email(form.email.data) is not None:
            flash('E-Mail bereits registriert', 'warning')
            return render_template('auth/register.html', form=form)
        first, last = split_name(form.name.data)
        member = create_member(first, last, email=form.email.data)
        create_user(member, form.email.data, password=form.password.data)
        db.session.commit()
        flash('Registrierung erfolgreich. Bitte einloggen.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('auth/register.html', form=form)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    next_url = safe_next(request.args.get('next'))
    if current_user.is_authenticated:
        return redirect(next_url or url_for('announcements.index'))
    form = LoginForm()
    if form.validate_on_submit():
        now = utcnow()
        user = find_user_by_login_email(form.email.data)
        if user is None:
            check_password_hash(_DUMMY_HASH, form.password.data)
        elif not User.reserve_login_attempt(
            user.id,
            now,
            max_attempts=current_app.config['MAX_FAILED_LOGINS'],
            lockout=timedelta(minutes=current_app.config['LOGIN_LOCKOUT_MINUTES']),
        ):
            # Locked: same message and same cost as a wrong password, so the response
            # does not reveal that the account exists. The lock expires on its own.
            check_password_hash(_DUMMY_HASH, form.password.data)
        elif user.check_password(form.password.data) and user.is_active:
            User.record_successful_login(user.id, now)
            login_user(user, remember=form.remember.data)
            return redirect(next_url or url_for('announcements.index'))
        flash(LOGIN_FAILED, 'danger')
    return render_template('auth/login.html', form=form)


@bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('Abgemeldet.', 'success')
    return redirect(url_for('auth.login'))
