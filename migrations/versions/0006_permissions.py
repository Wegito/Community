"""permissions: permission, role, role_permission, member_role; admin flag becomes a role

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-11 15:30:00

"""

from datetime import date

import sqlalchemy as sa
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

role_t = sa.table('role', sa.column('id', sa.Integer), sa.column('key', sa.String), sa.column('label', sa.String), sa.column('kind', sa.String))
member_role_t = sa.table(
    'member_role',
    sa.column('member_id', sa.Integer),
    sa.column('role_id', sa.Integer),
    sa.column('valid_from', sa.Date),
    sa.column('reason', sa.String),
)
user_t = sa.table('user', sa.column('id', sa.Integer), sa.column('member_id', sa.Integer), sa.column('role', sa.String))


def upgrade():
    op.create_table(
        'permission',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=80), nullable=False),
        sa.Column('module', sa.String(length=40), nullable=False),
        sa.Column('description', sa.String(length=200), nullable=False),
        sa.Column('is_sensitive', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_permission')),
        sa.UniqueConstraint('code', name=op.f('uq_permission_code')),
    )
    op.create_table(
        'role',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=80), nullable=False),
        sa.Column('label', sa.String(length=120), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_role')),
        sa.UniqueConstraint('key', name=op.f('uq_role_key')),
    )
    op.create_table(
        'member_role',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('member_id', sa.Integer(), nullable=False),
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.Column('valid_from', sa.Date(), nullable=False),
        sa.Column('valid_to', sa.Date(), nullable=True),
        sa.Column('granted_by_user_id', sa.Integer(), nullable=True),
        sa.Column('reason', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['granted_by_user_id'], ['user.id'], name=op.f('fk_member_role_granted_by_user_id_user')),
        sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_member_role_member_id_member')),
        sa.ForeignKeyConstraint(['role_id'], ['role.id'], name=op.f('fk_member_role_role_id_role')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_member_role')),
    )
    with op.batch_alter_table('member_role', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_member_role_member_id'), ['member_id'], unique=False)
    op.create_table(
        'role_permission',
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.Column('permission_id', sa.Integer(), nullable=False),
        sa.Column('granted_by_user_id', sa.Integer(), nullable=True),
        sa.Column('granted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['granted_by_user_id'], ['user.id'], name=op.f('fk_role_permission_granted_by_user_id_user')),
        sa.ForeignKeyConstraint(
            ['permission_id'], ['permission.id'], name=op.f('fk_role_permission_permission_id_permission'), ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(['role_id'], ['role.id'], name=op.f('fk_role_permission_role_id_role'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('role_id', 'permission_id', name=op.f('pk_role_permission')),
    )

    op.bulk_insert(
        role_t,
        [
            {'key': 'custom.sysadmin', 'label': 'Administrator', 'kind': 'custom'},
            {'key': 'system.former_member', 'label': 'Ehemaliges Mitglied', 'kind': 'system'},
        ],
    )

    # role='admin' becomes the sysadmin role. role='resident' was the default for every
    # account and carries no information, so it is dropped (Hausbewohner are set by hand).
    conn = op.get_bind()
    sysadmin_id = conn.execute(sa.select(role_t.c.id).where(role_t.c.key == 'custom.sysadmin')).scalar_one()
    for member_id in conn.execute(sa.select(user_t.c.member_id).where(user_t.c.role == 'admin')).scalars():
        conn.execute(
            member_role_t.insert().values(
                member_id=member_id, role_id=sysadmin_id, valid_from=date.today(), reason='Migration: user.role=admin'
            )
        )

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('role')


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('role', sa.String(length=20), server_default='resident', nullable=False))

    conn = op.get_bind()
    admins = conn.execute(
        sa.select(member_role_t.c.member_id)
        .join(role_t, role_t.c.id == member_role_t.c.role_id)
        .where(role_t.c.key == 'custom.sysadmin')
    ).scalars()
    for member_id in set(admins):
        conn.execute(user_t.update().where(user_t.c.member_id == member_id).values(role='admin'))

    op.drop_table('role_permission')
    with op.batch_alter_table('member_role', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_member_role_member_id'))
    op.drop_table('member_role')
    op.drop_table('role')
    op.drop_table('permission')
