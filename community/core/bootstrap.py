"""`flask setup`: run after every `flask db upgrade`, on the first install and on every update.

It copies the permission codes declared in code into the database, adds the default grants
from seed/authz.yaml where nobody can have decided otherwise yet, and says what the
installation still lacks: an admin, or ranks for members who have none.
"""

import click
from flask.cli import with_appcontext
from sqlalchemy import select

from community.extensions import MIGRATIONS_DIR, db


def _database_is_current():
    """True if every migration has run (`flask db upgrade`)."""
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option('script_location', MIGRATIONS_DIR)
    head = ScriptDirectory.from_config(config).get_current_head()
    with db.engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision() == head


def _has_admin():
    from community.core.authz.models import SYSADMIN, MemberRole, Role
    from community.core.authz.service import active_on
    from community.core.identity.models import Member, MemberStatus
    from community.core.time import today_local

    query = (
        select(MemberRole.id)
        .join(Role, Role.id == MemberRole.role_id)
        .join(Member, Member.id == MemberRole.member_id)
        .where(Role.key == SYSADMIN, Member.status == MemberStatus.ACTIVE, active_on(MemberRole, today_local()))
    )
    return db.session.scalar(query.limit(1)) is not None


@click.command('setup')
@click.option(
    '--seed', 'seed_all', is_flag=True, help='Also add every missing default grant (like `flask authz seed`).'
)
@with_appcontext
def setup(seed_all):
    """Permissions and default grants after `flask db upgrade`; then what is still missing.

    The default grants (seed/authz.yaml) are added on the first run, when no role has any grant yet
    (new install, update from M0). Later runs add only the grants of permission codes that are new
    in the database, so a grant removed on purpose stays removed; --seed adds every missing one.
    """
    from community.core.authz.models import Permission, RolePermission
    from community.core.authz.service import seed_grants, sync_permissions
    from community.core.org.services import members_without_rank

    if not _database_is_current():
        raise click.ClickException('The database is not up to date. Run `flask db upgrade` first.')
    first_run = db.session.scalar(select(RolePermission.role_id).limit(1)) is None
    known = set(db.session.scalars(select(Permission.code)))
    added, updated, stale = sync_permissions()
    click.echo(f'Permissions: {added} added, {updated} updated.')
    if stale:
        click.echo('No longer declared in code (grants still exist): ' + ', '.join(stale))
    new_codes = set(db.session.scalars(select(Permission.code))) - known
    if first_run or seed_all:
        result = seed_grants()
    elif new_codes:
        result = seed_grants(only_codes=new_codes)
    else:
        result = {'added': 0, 'missing_roles': []}
    click.echo(f'Default grants: {result["added"] or "none"} added.')
    if result['missing_roles']:
        click.echo('Roles in seed/authz.yaml but not in the database: ' + ', '.join(result['missing_roles']))

    if not _has_admin():
        click.echo('No admin yet: run `flask create-admin`.')
    rankless = members_without_rank()
    if rankless:
        click.echo('Members without a rank (they get rights only from a rank; a technical admin needs none):')
        for member in rankless:
            email = member.user.login_email if member.user else member.email
            click.echo(f'  {member.full_name}' + (f' <{email}>' if email else ''))
        click.echo(
            'An admin sets their first rank under Verwaltung → Ohne Rang, or the server operator runs\n'
            '  flask org set-rank --email <email> --rank <spefux|fux|bursch|alter_herr>'
        )
