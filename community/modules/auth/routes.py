import hmac
from datetime import timedelta

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from community.core.identity.models import User
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
        if db.session.scalar(select(User).where(User.email == form.email.data)):
            flash('E-Mail bereits registriert', 'warning')
            return render_template('auth/register.html', form=form)
        u = User(email=form.email.data, name=form.name.data)
        u.set_password(form.password.data)
        db.session.add(u)
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
        user = db.session.scalar(select(User).where(User.email == form.email.data))
        if user is None:
            check_password_hash(_DUMMY_HASH, form.password.data)
        elif user.is_locked(now):
            pass  # same message as a wrong password; the lock expires on its own
        elif user.check_password(form.password.data):
            user.register_successful_login()
            db.session.commit()
            login_user(user, remember=form.remember.data)
            return redirect(next_url or url_for('announcements.index'))
        else:
            user.register_failed_login(
                now,
                max_attempts=current_app.config['MAX_FAILED_LOGINS'],
                lockout=timedelta(minutes=current_app.config['LOGIN_LOCKOUT_MINUTES']),
            )
            db.session.commit()
        flash(LOGIN_FAILED, 'danger')
    return render_template('auth/login.html', form=form)


@bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('Abgemeldet.', 'success')
    return redirect(url_for('auth.login'))
