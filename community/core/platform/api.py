"""The JSON API under /api/v1 for devices such as the bar tablet (roadmap §7.3).

- A device logs in with `Authorization: Bearer cmt_<random>`. Only the SHA-256 hash and a short
  prefix of the token are stored; the token is shown once, when it is created, and can be revoked.
- Each token has scopes; a route declares the scope it needs with @api_scope (this is its access
  declaration, like @require_perm for pages). Modules declare their scopes with declare_scope().
- No cookies, no CSRF; errors are application/problem+json (401, 403, 404, 422).
"""

import hashlib
import secrets
from functools import wraps

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select
from werkzeug.exceptions import HTTPException

from community.core.db import UTCDateTime
from community.core.platform import audit
from community.core.time import utcnow
from community.extensions import db

TOKEN_PREFIX = 'cmt_'  # noqa: S105 (a prefix, not a secret)
SCOPES = {}

bp = Blueprint('api', __name__, url_prefix='/api/v1')


def declare_scope(scope, label):
    SCOPES[scope] = label
    return scope


class ApiClient(db.Model):
    """A device (e.g. "Theke-Tablet") that may call the API with its token."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    token_hint = db.Column(db.String(12), nullable=False)  # "cmt_ab12…", to recognise it in lists
    scopes = db.Column(db.String(300), nullable=False, default='')  # space-separated
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(UTCDateTime, nullable=False, default=utcnow)
    last_used_at = db.Column(UTCDateTime)
    revoked_at = db.Column(UTCDateTime)

    created_by = db.relationship('User')

    @property
    def scope_list(self):
        return self.scopes.split()

    @property
    def is_active(self):
        return self.revoked_at is None


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class ApiClientError(Exception):
    """Wrong input when creating a device; the message is German."""


def create_client(name, scopes, *, created_by):
    """Returns (client, token). The token is not stored and cannot be shown again. Commits nothing."""
    name = (name or '').strip()
    if not name:
        raise ApiClientError('Bitte einen Namen für das Gerät angeben.')
    unknown = [scope for scope in scopes if scope not in SCOPES]
    if unknown or not scopes:
        raise ApiClientError('Bitte mindestens eine gültige Berechtigung wählen.')
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    client = ApiClient(
        name=name,
        token_hash=_hash(token),
        token_hint=token[:8] + '…',
        scopes=' '.join(sorted(set(scopes))),
        created_by_user_id=getattr(created_by, 'id', None),
    )
    db.session.add(client)
    db.session.flush()
    audit.record('api.client.create', client, actor=created_by, diff={'name': name, 'scopes': client.scopes})
    return client, token


def revoke_client(client, *, revoked_by):
    if client.revoked_at is None:
        client.revoked_at = utcnow()
        audit.record('api.client.revoke', client, actor=revoked_by, diff={'name': client.name})


def clients():
    return db.session.scalars(select(ApiClient).order_by(ApiClient.revoked_at.is_not(None), ApiClient.name)).all()


def client_for_token(token):
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    client = db.session.scalar(select(ApiClient).where(ApiClient.token_hash == _hash(token)))
    return client if client is not None and client.is_active else None


def problem(status, title, detail=None, **extra):
    """An error response as application/problem+json (RFC 9457)."""
    body = {'type': 'about:blank', 'title': title, 'status': status, **extra}
    if detail:
        body['detail'] = detail
    response = jsonify(body)
    response.status_code = status
    response.mimetype = 'application/problem+json'
    if status == 401:
        response.headers['WWW-Authenticate'] = 'Bearer'
    return response


def current_client():
    return g.get('api_client')


def api_scope(scope):
    """The route needs a valid device token with `scope`; otherwise 401 or 403 as problem+json."""
    if scope not in SCOPES:
        raise ValueError(f'API scope {scope!r} is not declared; use declare_scope()')

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            header = request.headers.get('Authorization', '')
            token = header[7:].strip() if header[:7].lower() == 'bearer ' else None
            client = client_for_token(token)
            if client is None:
                return problem(401, 'Unauthorized', 'Gültiges Token im Authorization-Header fehlt.')
            if scope not in client.scope_list:
                return problem(403, 'Forbidden', f'Dem Token fehlt die Berechtigung {scope}.')
            client.last_used_at = utcnow()
            g.api_client = client
            return view(*args, **kwargs)

        wrapped._access = ('api', scope)
        return wrapped

    return decorator


@bp.errorhandler(HTTPException)
def _http_error(error):
    return problem(error.code, error.name, error.description)


def init_api(app):
    """Register the API blueprint after the modules added their routes to it."""
    from community.extensions import csrf

    csrf.exempt(bp)
    app.register_blueprint(bp)
