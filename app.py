
from flask import Flask, render_template, redirect, url_for, flash, request
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from models import DB, User, Announcement, Chore, Booking, Resource, Role
from forms import RegisterForm, LoginForm, AnnouncementForm, ChoreForm, BookingForm
from werkzeug.middleware.proxy_fix import ProxyFix
from config import Config
from datetime import datetime
from sqlalchemy import select

login_manager = LoginManager()
login_manager.login_view = 'login'
@login_manager.user_loader
def load_user(user_id):
    return DB.session.get(User, int(user_id))


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0)
    DB.init_app(app)
    login_manager.init_app(app)

    @app.route('/')
    def index():
        anns = Announcement.query.order_by(Announcement.created_at.desc()).limit(10).all()
        return render_template('index.html', anns=anns)

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        from config import Config
        form = RegisterForm()
        if form.validate_on_submit():
                if form.invite.data != Config.INVITE_CODE:
                    flash('Falscher Invite‑Code', 'danger')
                    return render_template('auth/register.html', form=form)
                if DB.session.scalar(select(User).where(User.email == form.email.data)):
                    flash('E‑Mail bereits registriert', 'warning')
                    return render_template('auth/register.html', form=form)
                u = User(email=form.email.data, name=form.name.data)
                u.set_password(form.password.data)
                if form.email.data == Config.ADMIN_EMAIL:
                    u.role = Role.ADMIN
                DB.session.add(u)
                DB.session.commit()
                flash('Registrierung erfolgreich. Bitte einloggen.', 'success')
                return redirect(url_for('login'))
        return render_template('auth/register.html', form=form)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        form = LoginForm()
        if form.validate_on_submit():
            user = DB.session.scalar(select(User).where(User.email == form.email.data))
            if user and user.check_password(form.password.data):
                login_user(user)
                return redirect(url_for('index'))
            flash('Login fehlgeschlagen', 'danger')
        return render_template('auth/login.html', form=form)

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        return redirect(url_for('index'))

    def admin_required():
        if not current_user.is_authenticated or current_user.role != Role.ADMIN:
            flash('Nur für Admins', 'warning')
        return False
    return True

# Announcements
    @app.route('/announcements')
    @login_required
    def announcements():
        anns = Announcement.query.order_by(Announcement.created_at.desc()).all()
        return render_template('announcements/list.html', anns=anns)

    @app.route('/announcements/new', methods=['GET','POST'])
    @login_required
    def announcement_new():
        if not admin_required():
            return redirect(url_for('announcements'))
    form = AnnouncementForm()
    if form.validate_on_submit():
        a = Announcement(title=form.title.data, body=form.body.data, author_id=current_user.id)
        DB.session.add(a)
        DB.session.commit()
        flash('Ankündigung erstellt', 'success')
        return redirect(url_for('announcements'))
    return render_template('announcements/create.html', form=form)

    # Chores
    @app.route('/chores')
    @login_required
    def chores():
        items = Chore.query.order_by(Chore.due_date.asc().nulls_last()).all()
        return render_template('chores/list.html', items=items)


    @app.route('/chores/edit/<int:cid>', methods=['GET','POST'])
    @login_required
    def chore_edit(cid):
        item = DB.session.get(Chore, cid)
        form = ChoreForm(obj=item)
        if form.validate_on_submit():
            form.populate_obj(item)
            DB.session.commit()
            flash('Gespeichert', 'success')
            return redirect(url_for('chores'))
        return render_template('chores/edit.html', form=form, item=item)

    # Bookings (simple)
    @app.route('/bookings', methods=['GET','POST'])
    @login_required
    def bookings():
        form = BookingForm()
        if form.validate_on_submit():
            # ensure resource exists
            r = DB.session.scalar(select(Resource).where(Resource.name==form.resource.data))
            if not r:
                r = Resource(name=form.resource.data)
                DB.session.add(r)
                DB.session.commit()
            b = Booking(resource_id=r.id, user_id=current_user.id,
                start=form.start.data, end=form.end.data)
            # basic rule: no overlap
            overlap = DB.session.execute(
                    select(Booking).where(
                        Booking.resource_id==r.id,
                        Booking.end>form.start.data,
                        Booking.start<form.end.data
                    )
            ).scalars().first()
            if overlap:
                flash('Zeitraum überschneidet sich mit bestehender Buchung', 'danger')
            else:
                DB.session.add(b)
                DB.session.commit()
                flash('Gebucht', 'success')
                return redirect(url_for('bookings'))
        # show recent
        items = DB.session.execute(
            select(Booking, Resource, User)
            .join(Resource, Booking.resource_id==Resource.id)
            .join(User, Booking.user_id==User.id)
            .order_by(Booking.start.desc())
            .limit(20)
        ).all()
        return render_template('bookings/calendar.html', form=form, items=items)
    return app