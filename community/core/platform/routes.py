"""The bell (/notifications) and each member's notification settings."""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm

from community.core.authz import require_login
from community.core.security import safe_next
from community.extensions import db

from . import notify

bp = Blueprint('notifications', __name__, template_folder='templates')


class ButtonForm(FlaskForm):
    """CSRF-only form for single-button actions."""


@bp.route('/notifications')
@require_login
def index():
    return render_template(
        'notifications/index.html',
        items=notify.notifications_for(current_user.member),
        unread=notify.unread_count(current_user.member),
        form=ButtonForm(),
    )


@bp.route('/notifications/read-all', methods=['POST'])
@require_login
def read_all():
    if ButtonForm().validate_on_submit():
        notify.mark_all_read(current_user.member)
        db.session.commit()
    return redirect(url_for('notifications.index'))


@bp.route('/notifications/<int:notification_id>/read', methods=['POST'])
@require_login
def open_notification(notification_id):
    """Mark as read and follow the notification's link."""
    item = db.session.get(notify.Notification, notification_id)
    if item is None or item.member_id != current_user.member_id:
        abort(404)
    if ButtonForm().validate_on_submit():
        notify.mark_read(item)
        db.session.commit()
    return redirect(safe_next(item.link) or url_for('notifications.index'))


@bp.route('/settings/notifications', methods=['GET', 'POST'])
@require_login
def settings():
    form = ButtonForm()
    member = current_user.member
    if form.validate_on_submit():
        enabled = {tuple(value.split(':', 1)) for value in request.form.getlist('on') if ':' in value}
        notify.save_preferences(member, enabled, actor=current_user)
        db.session.commit()
        flash('Einstellungen gespeichert', 'success')
        return redirect(url_for('notifications.settings'))
    return render_template(
        'notifications/settings.html',
        rows=notify.preferences(member),
        channels=notify.CHANNELS,
        channel_labels=notify.CHANNEL_LABELS,
        has_address=bool(notify.mail_address(member)),
        form=form,
    )
