"""API v1 for the bar tablet (roadmap §7.3). The contract is docs/api/openapi.yaml."""

from flask import jsonify, request
from sqlalchemy import select

from community.core.identity.models import Member, MemberStatus
from community.core.platform.api import api_scope, bp, current_client, declare_scope, problem
from community.core.time import today_local
from community.extensions import db

from . import services as drinks

declare_scope('products:read', 'Getränke und Preise lesen')
declare_scope('members:read', 'Namen der aktiven Mitglieder lesen')
declare_scope('drinks:write', 'Striche eintragen')


@bp.route('/products')
@api_scope('products:read')
def products():
    today = today_local()
    items = []
    for product in drinks.products():
        price = drinks.price_on(product, today)
        if price is not None:
            items.append(
                {
                    'id': product.id,
                    'name': product.name,
                    'price_cents': price,
                    'counts_as_beer': product.counts_as_beer,
                    'volume_ml': product.volume_ml,
                }
            )
    db.session.commit()  # last_used_at
    return jsonify({'items': items})


@bp.route('/members')
@api_scope('members:read')
def members():
    """Only what the tablet needs to show a name button: no emails, no phone numbers."""
    rows = db.session.scalars(
        select(Member).where(Member.status == MemberStatus.ACTIVE).order_by(Member.first_name, Member.last_name)
    ).all()
    db.session.commit()
    return jsonify({'items': [{'id': m.public_id, 'name': m.full_name, 'biername': m.biername} for m in rows]})


@bp.route('/drinks/consumptions', methods=['POST'])
@api_scope('drinks:write')
def consumptions():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return problem(422, 'Unprocessable Content', 'Erwartet wird JSON wie {"items": [...]}.')
    try:
        results = drinks.ingest(current_client(), body.get('items'))
    except drinks.DrinksError as error:
        db.session.rollback()
        return problem(422, 'Unprocessable Content', str(error))
    db.session.commit()
    return jsonify({'results': results})
