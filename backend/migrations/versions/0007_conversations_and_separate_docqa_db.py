"""code q&a conversations; document q&a moved to its own database

* qa_conversations + qa_entries.conversation_id. Existing questions become one "Earlier questions"
  conversation per (project, user).
* docqa_documents / docqa_entries are copied to the Document Q&A database and dropped from this one.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-03 10:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('qa_conversations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_qa_conversations_project_user', 'qa_conversations', ['project_id', 'user_id'], unique=False)
    with op.batch_alter_table('qa_entries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('conversation_id', sa.Integer(), nullable=True))
        batch_op.create_index('ix_qa_entries_conversation_id', ['conversation_id'], unique=False)
        batch_op.create_foreign_key('fk_qa_entries_conversation_id', 'qa_conversations', ['conversation_id'], ['id'],
                                    ondelete='CASCADE')

    conn = op.get_bind()
    groups = conn.execute(text("SELECT project_id, user_id, MIN(created_at), MAX(created_at) FROM qa_entries "
                               "GROUP BY project_id, user_id")).all()
    for project_id, user_id, first, last in groups:
        cid = conn.execute(text("INSERT INTO qa_conversations (project_id, user_id, title, created_at, updated_at) "
                                "VALUES (:p, :u, 'Earlier questions', :c, :m) RETURNING id"),
                           {"p": project_id, "u": user_id, "c": first, "m": last}).scalar_one()
        conn.execute(text("UPDATE qa_entries SET conversation_id = :c WHERE project_id = :p AND user_id = :u"),
                     {"c": cid, "p": project_id, "u": user_id})

    from app.services.docqa_legacy import move_to_docqa_database

    move_to_docqa_database(conn)
    tables = set(sa.inspect(conn).get_table_names())
    if 'docqa_entries' in tables:
        op.drop_table('docqa_entries')
    if 'docqa_documents' in tables:
        op.drop_table('docqa_documents')


def downgrade() -> None:
    # Document Q&A data stays in its own database; only empty legacy tables are recreated here.
    op.create_table('docqa_documents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False),
    sa.Column('filename', sa.String(length=512), nullable=False),
    sa.Column('format', sa.String(length=16), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('char_count', sa.Integer(), nullable=False),
    sa.Column('chunk_count', sa.Integer(), nullable=False),
    sa.Column('page_count', sa.Integer(), nullable=False),
    sa.Column('truncated', sa.Boolean(), nullable=False),
    sa.Column('embedded', sa.Boolean(), nullable=False),
    sa.Column('model_key', sa.String(length=512), nullable=False),
    sa.Column('chunks', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('docqa_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('response', sa.JSON(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('not_found', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['docqa_documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('qa_entries', schema=None) as batch_op:
        batch_op.drop_constraint('fk_qa_entries_conversation_id', type_='foreignkey')
        batch_op.drop_index('ix_qa_entries_conversation_id')
        batch_op.drop_column('conversation_id')
    op.drop_index('ix_qa_conversations_project_user', table_name='qa_conversations')
    op.drop_table('qa_conversations')
