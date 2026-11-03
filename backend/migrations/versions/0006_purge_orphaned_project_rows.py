"""purge rows left behind by deleted projects

SQLite did not cascade project deletes, so files, graph, rules and history of deleted projects stayed in the
database and could reappear under a new project that reused the id. This removes them once.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02 13:30:00
"""
from alembic import op


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.services.project_data import purge_orphans

    purge_orphans(op.get_bind())


def downgrade() -> None:
    pass  # deleted orphan rows cannot be restored
