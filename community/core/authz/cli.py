import click
from flask.cli import with_appcontext

from community.core.identity.services import find_user_by_login_email
from community.core.platform import audit
from community.extensions import db

from . import service


@click.group('authz')
def authz_cli():
    """Permissions: sync codes, seed default grants, give roles."""


@authz_cli.command('sync')
@with_appcontext
def sync():
    """Copy the permission codes declared in code into the database. Run after every deploy."""
    added, updated, stale = service.sync_permissions()
    click.echo(f'Permissions: {added} added, {updated} updated.')
    if stale:
        click.echo('No longer declared in code (grants still exist): ' + ', '.join(stale))


@authz_cli.command('seed')
@click.option('--file', 'path', type=click.Path(exists=True, dir_okay=False), help='Default: community/seed/authz.yaml')
@with_appcontext
def seed(path):
    """Add the default grants. Never removes grants. Run on first install, or on purpose."""
    service.sync_permissions()
    result = service.seed_grants(path)
    click.echo(f'Grants added: {result["added"]}.')
    if result['missing_roles']:
        click.echo('Roles not in the database (skipped): ' + ', '.join(result['missing_roles']))
    if result['missing_codes']:
        click.echo('Codes not declared yet (skipped): ' + ', '.join(result['missing_codes']))


def _member_for(email):
    user = find_user_by_login_email(email)
    if user is None:
        raise click.ClickException(f'No account with login email {email}')
    return user.member


@authz_cli.command('grant')
@click.option('--email', required=True)
@click.option('--role', 'role_key', required=True, help='e.g. custom.sysadmin')
@with_appcontext
def grant(email, role_key):
    """Give a member a custom or system role (bootstrap; normally done in the app)."""
    member = _member_for(email)
    try:
        service.grant_member_role(member, role_key, reason='flask authz grant')
    except LookupError as error:
        raise click.ClickException(str(error)) from None
    audit.record('authz.member_role.grant', member, diff={'role': role_key, 'via': 'cli'})
    db.session.commit()
    click.echo(f'{member.full_name}: {role_key} granted.')


@authz_cli.command('revoke')
@click.option('--email', required=True)
@click.option('--role', 'role_key', required=True)
@with_appcontext
def revoke(email, role_key):
    """End a member's custom or system role today."""
    member = _member_for(email)
    rows = service.end_member_role(member, role_key)
    audit.record('authz.member_role.revoke', member, diff={'role': role_key, 'via': 'cli'})
    db.session.commit()
    click.echo(f'{member.full_name}: {len(rows)} assignment(s) of {role_key} ended.')


@authz_cli.command('show')
@click.option('--email', required=True)
@with_appcontext
def show(email):
    """List a member's active roles and permissions."""
    member = _member_for(email)
    click.echo('Roles: ' + (', '.join(r.key for r in service.active_roles(member)) or '(none)'))
    click.echo('Permissions: ' + (', '.join(sorted(service.permissions_for(member))) or '(none)'))
