"""Tables of the Document Q&A database.

This database is separate from the main one (projects, code knowledge graph, Code Q&A). User ids are stored as
plain numbers: the users table lives in the main database, so there is no cross-database foreign key, and access
is checked in the API layer (a user only ever sees rows whose owner_id / user_id is their own id).
Chunk vectors live in this database too (pgvector table ``lnx_doc_vectors``) or in a file index under
STORAGE_DIR/docqa, never alongside the code vectors.
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import DocBase
from app.models import utcnow


class DocQADocument(DocBase):
    """A standalone document uploaded for the role-based Q&A feature, owned by one user."""

    __tablename__ = "docqa_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(Integer, index=True)
    filename: Mapped[str] = mapped_column(String(512))
    format: Mapped[str] = mapped_column(String(16))  # pdf | docx | txt | md
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="ready")  # ready | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    embedded: Mapped[bool] = mapped_column(Boolean, default=False)
    model_key: Mapped[str] = mapped_column(String(512), default="")
    chunks: Mapped[list] = mapped_column(JSON, default=list)  # [{id,index,page,section,title,text}]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DocQAConversation(DocBase):
    """One user's thread of questions about one document."""

    __tablename__ = "docqa_conversations"
    __table_args__ = (Index("ix_docqa_conversations_doc_user", "document_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("docqa_documents.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocQAEntry(DocBase):
    """One question-and-answer exchange against a document."""

    __tablename__ = "docqa_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("docqa_documents.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("docqa_conversations.id", ondelete="CASCADE"),
                                                        nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32))  # end_user | tester | developer | business
    response: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    not_found: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
