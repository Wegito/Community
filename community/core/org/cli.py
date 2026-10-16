from datetime import date

import click
from flask.cli import with_appcontext
from sqlalchemy import select

from community.core.identity.models import Member, normalize_email
from community.core.identity.services import find_user_by_login_email
from community.core.time import today_local
from community.extensions import db

from . import services


@click.group('org')
def org_cli():
    """Club structure: ranks (bootstrap helpers)."""


@org_cli.command('set-rank')
@click.option('--email', required=True, help='Login email of the member (or contact email if they have no login)')
@click.option('--rank', 'rank_key', required=True, help='spefux, fux, bursch or alter_herr')
@click.option('--from', 'valid_from', help='YYYY-MM-DD, default today')
@with_appcontext
def set_rank(email, rank_key, valid_from):
    """Set a member's rank as the server operator (bootstrap, e.g. for the first admin)."""
    user = find_user_by_login_email(email)
    member = user.member if user else db.session.scalar(select(Member).where(Member.email == normalize_email(email)))
    if member is None:
        raise click.ClickException(f'No member with email {email}')
    rank = services.rank_by_key(rank_key)
    if rank is None:
        raise click.ClickException(f'Unknown rank {rank_key!r}')
    day = date.fromisoformat(valid_from) if valid_from else today_local()
    try:
        services.change_rank(member, rank, day, changed_by=None, note='flask org set-rank')
    except services.OrgError as error:
        raise click.ClickException(str(error)) from None
    db.session.commit()
    click.echo(f'{member.full_name} is {rank.label} from {day:%Y-%m-%d}.')
