"""Error pages."""

from flask import flash, redirect, render_template, request, url_for

from community.extensions import db


def register_error_handlers(app):
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
        rule = request.url_rule
        if rule is not None and 'GET' in rule.methods:
            return redirect(request.path)
        return redirect(url_for('announcements.index'))

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        return render_template('errors/500.html'), 500
