import click
from email_validator import EmailNotValidError, validate_email
from flask.cli import with_appcontext

from community.core.platform import audit
from community.extensions import db

from .models import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, Role, normalize_email, split_name
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


@click.command('create-admin')
@click.option('--email', prompt=True, callback=_check_email)
@click.option('--name', prompt=True, callback=_check_name)
@click.password_option(callback=_check_password)
@with_appcontext
def create_admin(email, name, password):
    """Create an admin account, or promote an existing one and set its password."""
    user = find_user_by_login_email(email)
    if user is None:
        first, last = split_name(name)
        member = create_member(first, last, email=email)
        user = create_user(member, email)
    user.set_password(password)
    user.role = Role.ADMIN
    user.is_enabled = True
    user.failed_logins = 0
    user.locked_until = None
    audit.record('identity.create_admin', user, diff={'via': 'cli'})
    db.session.commit()
    click.echo(f'Admin ready: {email}')
