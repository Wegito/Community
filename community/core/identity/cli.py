import click
from flask.cli import with_appcontext
from sqlalchemy import select

from community.extensions import db

from .models import Role, User, normalize_email


@click.command('create-admin')
@click.option('--email', prompt=True)
@click.option('--name', prompt=True)
@click.password_option()
@with_appcontext
def create_admin(email, name, password):
    """Create an admin account, or promote an existing one."""
    email = normalize_email(email)
    user = db.session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, name=name)
        db.session.add(user)
    user.set_password(password)
    user.role = Role.ADMIN
    db.session.commit()
    click.echo(f'Admin ready: {email}')
