import hmac

from flask import current_app, flash, redirect, render_template, url_for
from flask_login import login_required, login_user, logout_user
from sqlalchemy import select

from community.core.identity.models import User
from community.extensions import db

from . import bp
from .forms import LoginForm, RegisterForm


@bp.route('/register', methods=['GET', 'POST'])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        invite_code = current_app.config.get('INVITE_CODE')
        if not invite_code or not hmac.compare_digest(form.invite.data.encode(), invite_code.encode()):
            flash('Falscher Invite‑Code', 'danger')
            return render_template('auth/register.html', form=form)
        if db.session.scalar(select(User).where(User.email == form.email.data)):
            flash('E‑Mail bereits registriert', 'warning')
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
    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(select(User).where(User.email == form.email.data))
        if user and user.check_password(form.password.data):
            login_user(user)
            return redirect(url_for('announcements.index'))
        flash('Login fehlgeschlagen', 'danger')
    return render_template('auth/login.html', form=form)


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('announcements.index'))
