from flask import render_template
from flask_login import current_user

from community.core.authz import current_permissions, require_login
from community.core.widgets import home_cards

from . import bp, services


@bp.route('/')
@require_login
def index():
    member = current_user.member
    return render_template(
        'home/index.html',
        header=services.header(member),
        cards=home_cards(member),
        former=not member.is_active_member,
        has_rights=bool(current_permissions()),
    )
