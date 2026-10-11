"""bookings: end after start, status, active resources

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-11 13:41:39.000067

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

booking = sa.table('booking', sa.column('id', sa.Integer), sa.column('start', sa.DateTime), sa.column('end', sa.DateTime))


def upgrade():
    # The new CHECK would reject old rows whose end is not after their start. Stop with a clear message.
    conn = op.get_bind()
    bad = conn.execute(sa.select(booking.c.id).where(booking.c.end <= booking.c.start)).scalars().all()
    if bad:
        raise RuntimeError(
            f'Bookings {bad} end before or when they start. Fix or delete them by hand, then upgrade again.'
        )

    with op.batch_alter_table('booking', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=20), server_default='active', nullable=False))
        batch_op.create_check_constraint(op.f('ck_booking_end_after_start'), '"end" > start')

    with op.batch_alter_table('resource', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False))


def downgrade():
    with op.batch_alter_table('resource', schema=None) as batch_op:
        batch_op.drop_column('is_active')

    with op.batch_alter_table('booking', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_booking_end_after_start'), type_='check')
        batch_op.drop_column('status')
