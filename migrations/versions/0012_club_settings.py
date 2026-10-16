"""club settings (name, short name)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-12 23:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    # No row yet: the name keeps coming from CLUB_NAME until someone saves it in the app.
    op.create_table('club',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('short_name', sa.String(length=24), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint('id = 1', name=op.f('ck_club_single_row')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_club'))
    )


def downgrade():
    op.drop_table('club')
