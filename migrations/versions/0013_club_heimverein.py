"""club: heimverein name

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-13 12:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('club', schema=None) as batch_op:
        batch_op.add_column(sa.Column('heimverein', sa.String(length=80), nullable=True))


def downgrade():
    with op.batch_alter_table('club', schema=None) as batch_op:
        batch_op.drop_column('heimverein')
