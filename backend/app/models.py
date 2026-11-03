"""ORM models."""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(32), default="analyst")  # admin | analyst | viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(32), default="created")  # created|uploaded|analyzing|ready|failed
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(255), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceFile(Base):
    __tablename__ = "source_files"
    __table_args__ = (UniqueConstraint("project_id", "path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(1024))
    language: Mapped[str] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(Integer)
    line_count: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))


class AnalysisArtifact(Base):
    """One JSON document per analysis output kind (graph, rules, lineage, ...)."""

    __tablename__ = "analysis_artifacts"
    __table_args__ = (UniqueConstraint("project_id", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict | list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GraphNode(Base):
    """One component of the Legacy Knowledge Graph (program, table, column, job, API, ...), queryable by SQL.

    Rows are rewritten on every analysis. Keeping the graph in tables (not one JSON document) lets the
    platform answer questions across files and, later, across applications in a portfolio.
    """

    __tablename__ = "graph_nodes"
    __table_args__ = (
        UniqueConstraint("project_id", "node_id"),
        Index("ix_graph_nodes_project_type", "project_id", "type"),
        Index("ix_graph_nodes_label", "label"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    node_id: Mapped[str] = mapped_column(String(512))  # e.g. "program:PAYMENT"
    type: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(512))
    file: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class GraphEdge(Base):
    """A relationship between two graph nodes (CALLS, READ_BY, WRITES, ...) with file:line evidence."""

    __tablename__ = "graph_edges"
    __table_args__ = (
        Index("ix_graph_edges_project_source", "project_id", "source"),
        Index("ix_graph_edges_project_target", "project_id", "target"),
        Index("ix_graph_edges_project_type", "project_id", "type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(512))
    target: Mapped[str] = mapped_column(String(512))
    type: Mapped[str] = mapped_column(String(32))
    evidence: Mapped[list] = mapped_column(JSON, default=list)  # [{file, line}]
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class BusinessRule(Base):
    """An extracted business rule with its human review status.

    The fingerprint identifies the same rule across re-analyses (rule numbers can shift), so a reviewer's
    decision is kept when the code is analysed again.
    """

    __tablename__ = "business_rules"
    __table_args__ = (
        UniqueConstraint("project_id", "rule_id"),
        Index("ix_business_rules_project_fingerprint", "project_id", "fingerprint"),
        Index("ix_business_rules_project_program", "project_id", "program"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    rule_id: Mapped[str] = mapped_column(String(16))  # BR-001
    fingerprint: Mapped[str] = mapped_column(String(64))
    program: Mapped[str] = mapped_column(String(255))
    file: Mapped[str] = mapped_column(String(1024))
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(32))
    is_technical: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # the full extracted rule
    review_status: Mapped[str] = mapped_column(String(16), default="candidate")  # candidate|accepted|rejected|edited
    review_note: Mapped[str] = mapped_column(Text, default="")
    # Why a rule was rejected: technical (plumbing such as return-code checks), obsolete, duplicate or other.
    review_reason: Mapped[str] = mapped_column(String(16), default="", server_default="")
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QAConversation(Base):
    """A Code Q&A conversation: one user's thread of questions about one project.

    Follow-up questions ("who calls it?", "what about CUSTOMER_MASTER?") are read against the earlier turns.
    Document Q&A keeps its own conversations in its own database (app.models_docqa).
    """

    __tablename__ = "qa_conversations"
    __table_args__ = (Index("ix_qa_conversations_project_user", "project_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QAEntry(Base):
    __tablename__ = "qa_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("qa_conversations.id", ondelete="CASCADE"),
                                                        nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    question: Mapped[str] = mapped_column(Text)
    response: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    not_found: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AISettings(Base):
    """Organisation-wide AI model configuration. A single row (id=1)."""

    __tablename__ = "ai_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), default="none")
    model: Mapped[str] = mapped_column(String(255), default="")
    base_url: Mapped[str] = mapped_column(String(1024), default="")
    api_version: Mapped[str] = mapped_column(String(64), default="")
    deployment: Mapped[str] = mapped_column(String(255), default="")
    encrypted_api_key: Mapped[str] = mapped_column(Text, default="")
    temperature: Mapped[float] = mapped_column(Float, default=0.0)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=1200)
    top_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_char_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_retrieval_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    monthly_token_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)  # whole organisation; None = no cap
    project_token_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)  # per project per month
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class LLMCacheEntry(Base):
    """A model response keyed by the hash of model + prompt version + scrubbed prompt. Reused, never re-paid."""

    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(255))
    response_text: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LLMUsage(Base):
    """One row per model request: what was sent (size, hash, sources, redactions) to which model, and its cost."""

    __tablename__ = "llm_usage"
    __table_args__ = (Index("ix_llm_usage_created", "created_at"), Index("ix_llm_usage_project", "project_id"))

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    purpose: Mapped[str] = mapped_column(String(64))  # ask | doc_qa | summary | rule_explain | test
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(255))
    tier: Mapped[str] = mapped_column(String(16), default="large")  # small | large
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    prompt_chars: Mapped[int] = mapped_column(Integer, default=0)
    prompt_sha256: Mapped[str] = mapped_column(String(64), default="")
    redactions: Mapped[int] = mapped_column(Integer, default=0)
    sources: Mapped[list] = mapped_column(JSON, default=list)  # files and line ranges included in the prompt
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AIModel(Base):
    """A saved AI model connection. Many can be saved; exactly one (or none) is active.

    The active entry is mirrored into AISettings, which the Q&A features read.
    """

    __tablename__ = "ai_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(255), default="")
    base_url: Mapped[str] = mapped_column(String(1024), default="")
    api_version: Mapped[str] = mapped_column(String(64), default="")
    deployment: Mapped[str] = mapped_column(String(255), default="")
    encrypted_api_key: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)  # the main (large) model
    is_small: Mapped[bool] = mapped_column(Boolean, default=False)  # cheap model for summaries and routing
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False)  # tried when the main model fails
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class PasswordResetToken(Base):
    """One-time password reset link. Only a hash of the token is stored."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(255), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class EmbeddingSettings(Base):
    """Organisation-wide semantic search configuration. A single row (id=1)."""

    __tablename__ = "embedding_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[str] = mapped_column(String(64), default="none")
    model: Mapped[str] = mapped_column(String(255), default="")
    base_url: Mapped[str] = mapped_column(String(1024), default="")
    api_version: Mapped[str] = mapped_column(String(64), default="")
    deployment: Mapped[str] = mapped_column(String(255), default="")
    encrypted_api_key: Mapped[str] = mapped_column(Text, default="")
    reuse_llm_key: Mapped[bool] = mapped_column(Boolean, default=False)
    batch_size: Mapped[int] = mapped_column(Integer, default=32)
    max_chars: Mapped[int] = mapped_column(Integer, default=4000)
    semantic_weight: Mapped[float] = mapped_column(Float, default=1.0)
    min_semantic_score: Mapped[float] = mapped_column(Float, default=0.35)
    auto_index: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class EmbeddingIndex(Base):
    """State of a project's semantic index (one row per project)."""

    __tablename__ = "embedding_indexes"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="none")  # none | building | ready | failed
    model_key: Mapped[str] = mapped_column(String(512), default="")
    store: Mapped[str] = mapped_column(String(32), default="")
    dim: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    embedded_count: Mapped[int] = mapped_column(Integer, default=0)
    analysis_version: Mapped[str] = mapped_column(String(64), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
