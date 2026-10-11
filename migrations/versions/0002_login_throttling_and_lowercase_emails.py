"""login throttling and lowercase emails

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-11 13:39:04.163411

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

user = sa.table('user', sa.column('id', sa.Integer), sa.column('email', sa.String))


def upgrade():
    # Emails are now stored trimmed and lowercase. Stop if that would merge two accounts.
    conn = op.get_bind()
    rows = conn.execute(sa.select(user.c.id, user.c.email)).all()
    seen = {}
    for row in rows:
        normalized = row.email.strip().lower()
        if normalized in seen:
            raise RuntimeError(
                f'Users {seen[normalized]} and {row.id} both have the email {normalized!r} after lowercasing. '
                'Merge or rename one of them by hand, then run the upgrade again.'
            )
        seen[normalized] = row.id
    for row in rows:
        normalized = row.email.strip().lower()
        if normalized != row.email:
            conn.execute(sa.update(user).where(user.c.id == row.id).values(email=normalized))

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('failed_logins', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('locked_until', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('locked_until')
        batch_op.drop_column('failed_logins')
