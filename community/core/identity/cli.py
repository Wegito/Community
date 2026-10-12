import click
from email_validator import EmailNotValidError, validate_email
from flask.cli import with_appcontext
from sqlalchemy import select

from community.core.platform import audit
from community.extensions import db

from .models import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, Member, MemberStatus, normalize_email, split_name
from .services import create_member, create_user, find_user_by_login_email


def _check_email(ctx, param, value):
    value = normalize_email(value)
    try:
        validate_email(value, check_deliverability=False)
    except EmailNotValidError as error:
        raise click.BadParameter(str(error)) from None
    return value


def _check_name(ctx, param, value):
    value = value.strip()
    if not value or len(value) > 120:
        raise click.BadParameter('Name must be 1 to 120 characters.')
    return value


def _check_password(ctx, param, value):
    if not MIN_PASSWORD_LENGTH <= len(value) <= MAX_PASSWORD_LENGTH:
        raise click.BadParameter(f'Password must be {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters.')
    return value


def _login_for(email, name):
    """The account with this login email; else a new login for the member with this contact email
    (e.g. imported, not joined yet; the name is not changed), else a new member with login."""
    user = find_user_by_login_email(email)
    if user is not None:
        member = user.member
    else:
        member = db.session.scalar(select(Member).where(Member.email == email))
        if member is not None and member.user is not None:
            raise click.ClickException(
                f'{email} is the contact email of {member.full_name}, who logs in with '
                f'{member.user.login_email}. Run create-admin with that address.'
            )
    if member is not None and member.status != MemberStatus.ACTIVE:
        raise click.ClickException(f'{member.full_name} is not an active member.')
    if user is None:
        if member is None:
            first, last = split_name(name)
            member = create_member(first, last, email=email)
        else:
            click.echo(f'Login for the existing member {member.full_name}.')
        user = create_user(member, email)
    return user


@click.command('create-admin')
@click.option('--email', prompt=True, callback=_check_email)
@click.option('--name', prompt=True, callback=_check_name)
@click.password_option(callback=_check_password)
@with_appcontext
def create_admin(email, name, password):
    """Create an admin account, or promote an existing one and set its password.

    Admin = the custom.sysadmin role (technical administration, no finance rights).
    Run `flask setup` once before, so the role has its default permissions.
    """
    from community.core.authz.models import SYSADMIN
    from community.core.authz.service import active_roles, grant_member_role, role_by_key, role_permissions

    user = _login_for(email, name)
    user.set_password(password)
    if SYSADMIN not in {role.key for role in active_roles(user.member)}:
        grant_member_role(user.member, SYSADMIN, reason='flask create-admin')
    user.is_enabled = True
    user.failed_logins = 0
    user.locked_until = None
    audit.record('identity.create_admin', user, diff={'via': 'cli'})
    db.session.commit()
    click.echo(f'Admin ready: {email}')
    if not role_permissions({role_by_key(SYSADMIN).id}):
        click.echo('Warning: the admin role has no permissions yet. Run `flask setup`.')
