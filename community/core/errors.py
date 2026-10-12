"""Error pages."""

from flask import flash, redirect, render_template, request, url_for
from flask_wtf.csrf import CSRFError

from community.core.security import safe_next
from community.extensions import db


def _back_to_form():
    """A refused POST goes back to its page when that page opens with GET, otherwise to the start page."""
    rule = request.url_rule
    if rule is not None and 'GET' in rule.methods:
        return redirect(safe_next(request.full_path.rstrip('?')) or url_for('announcements.index'))
    return redirect(url_for('announcements.index'))


def register_error_handlers(app):
    @app.errorhandler(CSRFError)
    def csrf_failed(error):
        """The form's CSRF token expired (WTF_CSRF_TIME_LIMIT) or the session cookie is gone."""
        flash('Die Seite war zu lange offen. Bitte noch einmal versuchen.', 'warning')
        return _back_to_form()

    @app.errorhandler(403)
    def forbidden(error):
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template('errors/404.html'), 404

    @app.errorhandler(413)
    def too_large(error):
        """An upload over MAX_CONTENT_LENGTH: back to the form with a message, not a bare error page."""
        limit = app.config.get('MAX_CONTENT_LENGTH')
        size = f' (höchstens {limit / (1024 * 1024):g} MB)' if limit else ''
        flash(f'Die Datei ist zu groß{size}.', 'danger')
        return _back_to_form()

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        return render_template('errors/500.html'), 500
