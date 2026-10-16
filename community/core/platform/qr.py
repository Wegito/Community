"""QR codes: inline SVG (segno) and printed codes that lead to a page (roadmap D10).

A printed code holds only /q/<token>. The token names a *target* (e.g. "laundry"), never a
person, and GET /q/<token> only redirects: opening it can never write anything. A module
registers what a target means with @qr_handler('laundry'). Rotating a token makes old
stickers stop working (e.g. after a sticker was swapped).
"""

import secrets

import segno
from flask import Blueprint, abort, redirect
from markupsafe import Markup
from sqlalchemy import select

from community.core.authz import public
from community.core.db import TimestampMixin
from community.extensions import db

# target_type -> function(qr_token) returning a redirect target (URL)
QR_HANDLERS = {}


def qr_handler(target_type):
    def decorator(fn):
        QR_HANDLERS[target_type] = fn
        return fn

    return decorator


class QrToken(TimestampMixin, db.Model):
    __tablename__ = 'qr_token'

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False, default=lambda: new_token())
    target_type = db.Column(db.String(40), nullable=False)
    target_id = db.Column(db.Integer)
    label = db.Column(db.String(80), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)


def new_token():
    return secrets.token_urlsafe(16)


def tokens_for(target_type, target_id=None):
    query = select(QrToken).where(QrToken.target_type == target_type, QrToken.active)
    if target_id is not None:
        query = query.where(QrToken.target_id == target_id)
    return db.session.scalars(query.order_by(QrToken.id)).all()


def rotate(qr):
    """Give the code a new token; printed stickers with the old one stop working. Commits nothing."""
    qr.token = new_token()
    return qr


def qr_svg(data, *, title=None, scale=6):
    """An <svg> element encoding `data`, ready to put into a template.

    Error correction M survives a scratched screen or print; the quiet zone (border) is part
    of the image, so the code scans on dark backgrounds too.
    """
    code = segno.make(data, error='m', micro=False)
    # segno escapes the title and builds the rest itself, so the markup is safe.
    return Markup(code.svg_inline(scale=scale, title=title, dark='#000', light='#fff'))  # noqa: S704


bp = Blueprint('qr', __name__)


@bp.route('/q/<token>')
@public
def resolve(token):
    qr = db.session.scalar(select(QrToken).where(QrToken.token == token, QrToken.active))
    handler = QR_HANDLERS.get(qr.target_type) if qr else None
    if handler is None:
        abort(404)
    response = redirect(handler(qr))
    # The token is printed on a sticker, not secret, but keep it out of caches and Referer headers anyway.
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response
