"""document q&a database: documents, conversations, entries

Revision ID: d0001
Revises:
Create Date: 2026-11-03 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'd0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_docqa_documents_owner_id', 'docqa_documents', ['owner_id'], unique=False)

    op.create_table('docqa_conversations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['docqa_documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_docqa_conversations_doc_user', 'docqa_conversations', ['document_id', 'user_id'], unique=False)

    op.create_table('docqa_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('response', sa.JSON(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('not_found', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['docqa_documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['conversation_id'], ['docqa_conversations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_docqa_entries_document_id', 'docqa_entries', ['document_id'], unique=False)
    op.create_index('ix_docqa_entries_conversation_id', 'docqa_entries', ['conversation_id'], unique=False)


def downgrade() -> None:
    op.drop_table('docqa_entries')
    op.drop_table('docqa_conversations')
    op.drop_table('docqa_documents')
