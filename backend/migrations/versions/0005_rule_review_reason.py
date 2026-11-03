"""rule review reason

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-02 12:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('business_rules') as batch:
        batch.add_column(sa.Column('review_reason', sa.String(length=16), nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('business_rules') as batch:
        batch.drop_column('review_reason')
