"""Append-only audit log: who did what, when.

record() adds an entry to the current database session, so it is committed
(or rolled back) together with the change it describes.
"""

import json
import uuid
from datetime import timedelta

import click
from flask import g, has_request_context, request
from flask.cli import with_appcontext
from flask_login import current_user
from sqlalchemy import update

from community.core.db import UTCDateTime
from community.core.time import utcnow
from community.extensions import db


class AuditLog(db.Model):
    __tablename__ = 'audit_log'

    id = db.Column(db.Integer, primary_key=True)
    occurred_at = db.Column(UTCDateTime, nullable=False, default=utcnow, index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)
    action = db.Column(db.String(80), nullable=False, index=True)
    entity_type = db.Column(db.String(40))
    entity_id = db.Column(db.String(64))
    diff_json = db.Column(db.Text)
    ip = db.Column(db.String(45))
    request_id = db.Column(db.String(32))

    actor = db.relationship('User', lazy='joined')

    @property
    def diff(self):
        return json.loads(self.diff_json) if self.diff_json else None


def record(action, entity=None, *, entity_type=None, entity_id=None, diff=None, actor=None):
    """Add an audit entry. `entity` may be a model instance (type and id are taken from it)."""
    if entity is not None:
        entity_type = entity_type or entity.__class__.__name__.lower()
        entity_id = entity_id if entity_id is not None else getattr(entity, 'id', None)
    if actor is None and has_request_context() and current_user and current_user.is_authenticated:
        actor = current_user
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        diff_json=json.dumps(diff, ensure_ascii=False, default=str, sort_keys=True) if diff else None,
        actor_user_id=getattr(actor, 'id', None),
        ip=request.remote_addr if has_request_context() else None,
        request_id=g.get('request_id') if has_request_context() else None,
    )
    db.session.add(entry)
    return entry


def changes(obj, fields, before):
    """Return {field: [old, new]} for fields whose value differs from the `before` snapshot."""
    result = {}
    for field in fields:
        new = getattr(obj, field)
        if before.get(field) != new:
            result[field] = [before.get(field), new]
    return result


def snapshot(obj, fields):
    return {field: getattr(obj, field) for field in fields}


def init_request_ids(app):
    @app.before_request
    def assign_request_id():
        g.request_id = uuid.uuid4().hex

    @app.after_request
    def expose_request_id(response):
        if 'request_id' in g:
            response.headers['X-Request-ID'] = g.request_id
        return response


@click.group('audit')
def audit_cli():
    """Audit log maintenance."""


def purge_old_ips():
    """Remove the IP address from entries older than AUDIT_IP_RETENTION_DAYS. Commits nothing.

    `flask jobs tick` runs this once a day (job audit.purge_ips). Returns the number of entries changed.
    """
    from flask import current_app

    cutoff = utcnow() - timedelta(days=current_app.config['AUDIT_IP_RETENTION_DAYS'])
    result = db.session.execute(
        update(AuditLog)
        .where(AuditLog.occurred_at < cutoff, AuditLog.ip.is_not(None))
        .values(ip=None)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


@audit_cli.command('purge-ips')
@with_appcontext
def purge_ips():
    """Remove IP addresses older than AUDIT_IP_RETENTION_DAYS now (`flask jobs tick` does it daily)."""
    count = purge_old_ips()
    db.session.commit()
    click.echo(f'Removed IP addresses from {count} entries.')
