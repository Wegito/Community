"""members: split people (member) from login accounts (user)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-11 15:00:00

"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

user_t = sa.table(
    'user',
    sa.column('id', sa.Integer),
    sa.column('email', sa.String),
    sa.column('name', sa.String),
    sa.column('member_id', sa.Integer),
    sa.column('created_at', sa.DateTime),
)
member_t = sa.table(
    'member',
    sa.column('id', sa.Integer),
    sa.column('public_id', sa.String),
    sa.column('first_name', sa.String),
    sa.column('last_name', sa.String),
    sa.column('email', sa.String),
    sa.column('payment_ref', sa.String),
    sa.column('created_at', sa.DateTime),
)


def upgrade():
    op.create_table(
        'member',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('public_id', sa.String(length=36), nullable=False),
        sa.Column('first_name', sa.String(length=80), nullable=False),
        sa.Column('last_name', sa.String(length=80), server_default='', nullable=False),
        sa.Column('biername', sa.String(length=80), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('phone', sa.String(length=40), nullable=True),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=10), server_default='active', nullable=False),
        sa.Column('joined_on', sa.Date(), nullable=True),
        sa.Column('ended_on', sa.Date(), nullable=True),
        sa.Column('ended_reason', sa.String(length=40), nullable=True),
        sa.Column('payment_ref', sa.String(length=20), nullable=True),
        sa.Column('show_phone', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('show_address', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('pseudonymized_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_member')),
        sa.UniqueConstraint('email', name=op.f('uq_member_email')),
        sa.UniqueConstraint('payment_ref', name=op.f('uq_member_payment_ref')),
        sa.UniqueConstraint('public_id', name=op.f('uq_member_public_id')),
    )

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('member_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('is_enabled', sa.Boolean(), server_default=sa.true(), nullable=False))
        batch_op.add_column(sa.Column('session_version', sa.Integer(), server_default='1', nullable=False))
        batch_op.add_column(sa.Column('last_login_at', sa.DateTime(), nullable=True))

    # One member per existing user; the name is split at the first space.
    conn = op.get_bind()
    for row in conn.execute(sa.select(user_t.c.id, user_t.c.email, user_t.c.name, user_t.c.created_at)).all():
        parts = (row.name or '').strip().split(None, 1) or [row.email.split('@')[0]]
        public_id = str(uuid.uuid4())
        conn.execute(
            member_t.insert().values(
                public_id=public_id,
                first_name=parts[0],
                last_name=parts[1] if len(parts) > 1 else '',
                email=row.email,
                created_at=row.created_at,
            )
        )
        member_id = conn.execute(sa.select(member_t.c.id).where(member_t.c.public_id == public_id)).scalar_one()
        conn.execute(member_t.update().where(member_t.c.id == member_id).values(payment_ref=f'M-{member_id:04d}'))
        conn.execute(user_t.update().where(user_t.c.id == row.id).values(member_id=member_id))

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column('member_id', existing_type=sa.Integer(), nullable=False)
        batch_op.create_unique_constraint(batch_op.f('uq_user_member_id'), ['member_id'])
        batch_op.create_foreign_key(batch_op.f('fk_user_member_id_member'), 'member', ['member_id'], ['id'])
        batch_op.drop_constraint('uq_user_email', type_='unique')
        batch_op.alter_column('email', new_column_name='login_email', existing_type=sa.String(length=255))
        batch_op.alter_column('password_hash', existing_type=sa.String(length=255), nullable=True)
        batch_op.drop_column('name')

    # A separate batch: in the same batch as the rename, SQLite loses the new constraint.
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.create_unique_constraint(batch_op.f('uq_user_login_email'), ['login_email'])


def downgrade():
    # Names go back onto the user row; member-only people (no login) are lost.
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('name', sa.String(length=120), server_default='', nullable=False))

    conn = op.get_bind()
    user_m = sa.table('user', sa.column('id', sa.Integer), sa.column('member_id', sa.Integer), sa.column('name', sa.String))
    rows = conn.execute(
        sa.select(user_m.c.id, member_t.c.first_name, member_t.c.last_name).join(
            member_t, member_t.c.id == user_m.c.member_id
        )
    ).all()
    for row in rows:
        conn.execute(
            user_m.update().where(user_m.c.id == row.id).values(name=f'{row.first_name} {row.last_name}'.strip())
        )
    conn.execute(sa.text('UPDATE "user" SET password_hash = \'\' WHERE password_hash IS NULL'))

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column('password_hash', existing_type=sa.String(length=255), nullable=False)
        batch_op.drop_constraint(batch_op.f('uq_user_login_email'), type_='unique')
        batch_op.alter_column('login_email', new_column_name='email', existing_type=sa.String(length=255))

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_user_email', ['email'])
        batch_op.drop_constraint(batch_op.f('fk_user_member_id_member'), type_='foreignkey')
        batch_op.drop_constraint(batch_op.f('uq_user_member_id'), type_='unique')
        batch_op.drop_column('last_login_at')
        batch_op.drop_column('session_version')
        batch_op.drop_column('is_enabled')
        batch_op.drop_column('member_id')

    op.drop_table('member')
