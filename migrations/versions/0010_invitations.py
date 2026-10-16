"""invitations

Personal, single-use invitation links (roadmap M1-7). Only the SHA-256 hash of the token
is stored. Replaces the shared INVITE_CODE registration; no data to migrate.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-11 16:25:56.699359

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('invitation',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('member_id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('channel', sa.String(length=10), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('created_by_user_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('accepted_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['user.id'], name=op.f('fk_invitation_created_by_user_id_user')),
    sa.ForeignKeyConstraint(['member_id'], ['member.id'], name=op.f('fk_invitation_member_id_member')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_invitation')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_invitation_token_hash'))
    )
    with op.batch_alter_table('invitation', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_invitation_member_id'), ['member_id'], unique=False)


def downgrade():
    with op.batch_alter_table('invitation', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_invitation_member_id'))

    op.drop_table('invitation')
