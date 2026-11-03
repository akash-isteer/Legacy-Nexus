"""Semantic search: embedding settings, per-project vector index building, and query-time search."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.engine.vectors import get_store
from app.llm.embeddings import EMBEDDING_CATALOG, EmbeddingConfig, embed_texts
from app.models import AISettings, EmbeddingIndex, EmbeddingSettings, Project
from app.security import decrypt_secret, encrypt_secret, mask_secret
from app.services import artifacts

log = logging.getLogger("legacynexus.semantic")
_building: set[int] = set()
_lock = threading.Lock()

EDITABLE = ("enabled", "provider", "model", "base_url", "api_version", "deployment", "reuse_llm_key",
            "batch_size", "max_chars", "semantic_weight", "min_semantic_score", "auto_index")


# ------------------------------------------------------------------ settings

def get_row(db: Session) -> EmbeddingSettings:
    row = db.get(EmbeddingSettings, 1)
    if row is None:
        row = EmbeddingSettings(id=1)
        db.add(row)
        db.commit()
    return row


def _api_key(db: Session, row: EmbeddingSettings) -> str:
    if row.reuse_llm_key:
        ai = db.get(AISettings, 1)
        if ai is not None and ai.provider == row.provider:
            return decrypt_secret(ai.encrypted_api_key)
    return decrypt_secret(row.encrypted_api_key)


def to_config(db: Session, row: EmbeddingSettings) -> EmbeddingConfig:
    base_url = row.base_url
    if row.reuse_llm_key and not base_url and row.provider == "azure_openai":
        ai = db.get(AISettings, 1)
        base_url = ai.base_url if ai and ai.provider == "azure_openai" else ""
    return EmbeddingConfig(
        provider=row.provider, model=row.model, api_key=_api_key(db, row), base_url=base_url,
        api_version=row.api_version, deployment=row.deployment, batch_size=row.batch_size, max_chars=row.max_chars,
        timeout_seconds=get_settings().embedding_request_timeout_seconds,
    )


def to_public(db: Session, row: EmbeddingSettings) -> dict:
    cfg = to_config(db, row)
    own_key = decrypt_secret(row.encrypted_api_key)
    return {
        "enabled": row.enabled,
        "provider": row.provider,
        "model": row.model,
        "base_url": row.base_url,
        "api_version": row.api_version,
        "deployment": row.deployment,
        "api_key_set": bool(own_key),
        "api_key_masked": mask_secret(own_key),
        "reuse_llm_key": row.reuse_llm_key,
        "batch_size": row.batch_size,
        "max_chars": row.max_chars,
        "semantic_weight": row.semantic_weight,
        "min_semantic_score": row.min_semantic_score,
        "auto_index": row.auto_index,
        "configured": cfg.configured,
        "active": row.enabled and cfg.configured,
        "model_key": cfg.key if cfg.configured else None,
        "vector_store": get_store().name,
        "updated_at": row.updated_at,
    }


def update(db: Session, row: EmbeddingSettings, data: dict, user_id: int) -> EmbeddingSettings:
    if "provider" in data and data["provider"] not in EMBEDDING_CATALOG and data["provider"] != "none":
        raise ValueError(f"Unknown embedding provider {data['provider']}")
    for field in EDITABLE:
        if field in data and data[field] is not None:
            setattr(row, field, data[field].strip() if isinstance(data[field], str) else data[field])
    if data.get("clear_api_key"):
        row.encrypted_api_key = ""
    elif data.get("api_key"):
        row.encrypted_api_key = encrypt_secret(data["api_key"].strip())
    row.updated_by = user_id
    db.commit()
    return row


def active_config(db: Session) -> EmbeddingConfig | None:
    row = get_row(db)
    cfg = to_config(db, row)
    return cfg if row.enabled and cfg.configured else None


# ------------------------------------------------------------------ index state

def analysis_version(project: Project) -> str:
    return project.analyzed_at.isoformat() if project.analyzed_at else ""


def index_state(db: Session, project: Project) -> dict:
    """Status of a project's semantic index relative to the current settings and analysis."""
    row = get_row(db)
    cfg = to_config(db, row)
    idx = db.get(EmbeddingIndex, project.id)
    configured = row.enabled and cfg.configured
    state = {
        "enabled": configured,
        "status": idx.status if idx else "none",
        "model_key": idx.model_key if idx else None,
        "chunk_count": idx.chunk_count if idx else 0,
        "embedded_count": idx.embedded_count if idx else 0,
        "dim": idx.dim if idx else 0,
        "store": idx.store if idx else None,
        "error": idx.error if idx else None,
        "updated_at": idx.updated_at if idx else None,
        "building": is_building(project.id),
    }
    stale_reason = None
    if idx and idx.status == "ready" and configured:
        if idx.model_key != cfg.key:
            stale_reason = "The embedding model changed since this index was built."
        elif idx.analysis_version != analysis_version(project):
            stale_reason = "The code was re-analysed since this index was built."
        elif idx.store != get_store().name:
            stale_reason = "The vector store changed since this index was built."
    state["stale_reason"] = stale_reason
    state["usable"] = bool(configured and idx and idx.status == "ready" and not stale_reason)
    state["search_mode"] = "hybrid" if state["usable"] else "keyword"
    return state


def is_building(project_id: int) -> bool:
    with _lock:
        return project_id in _building


# ------------------------------------------------------------------ build

def build_index(project_id: int) -> None:
    """Embed every code chunk of a project and store the vectors. Runs as a background job."""
    with _lock:
        if project_id in _building:
            return
        _building.add(project_id)
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        cfg = active_config(db)
        if project is None or cfg is None or project.status != "ready":
            return
        chunks = artifacts.load(db, project_id, "chunks") or []
        idx = db.get(EmbeddingIndex, project_id) or EmbeddingIndex(project_id=project_id)
        db.add(idx)
        idx.status, idx.error, idx.model_key = "building", None, cfg.key
        idx.chunk_count, idx.embedded_count = len(chunks), 0
        db.commit()

        def progress(done: int, total: int) -> None:
            idx.embedded_count = done
            db.commit()

        texts = [f"{c['title']}\n{c['text']}" for c in chunks]
        vectors = embed_texts(cfg, texts, kind="document", progress=progress)
        store = get_store()
        store.save(project_id, cfg.key, [c["id"] for c in chunks], vectors)
        idx.status, idx.store, idx.dim = "ready", store.name, len(vectors[0]) if vectors else 0
        idx.embedded_count = len(vectors)
        idx.analysis_version = analysis_version(project)
        db.commit()
        log.info("Semantic index ready for project %s: %s chunks, model %s, store %s",
                 project_id, len(vectors), cfg.key, store.name)
    except Exception as exc:  # report on the index row; keyword search keeps working
        log.exception("Semantic indexing failed for project %s", project_id)
        db.rollback()
        idx = db.get(EmbeddingIndex, project_id)
        if idx:
            idx.status, idx.error = "failed", str(exc)[:2000]
            db.commit()
    finally:
        db.close()
        with _lock:
            _building.discard(project_id)


def delete_index(db: Session, project_id: int) -> None:
    try:
        get_store().delete(project_id)
    except Exception:  # best effort: project rows are being deleted anyway
        log.warning("Could not delete vectors for project %s", project_id)


# ------------------------------------------------------------------ query time

@dataclass
class ProjectSemanticSearch:
    """Implements engine.qa.SemanticSearch for one project and the current embedding model."""

    project_id: int
    cfg: EmbeddingConfig
    min_score: float
    weight: float
    label: str

    def search(self, question: str, top_k: int) -> list[tuple[str, float]]:
        query_vector = embed_texts(self.cfg, [question], kind="query")[0]
        return get_store().search(self.project_id, self.cfg.key, query_vector, top_k)


def searcher_for(db: Session, project: Project) -> tuple[ProjectSemanticSearch | None, dict]:
    state = index_state(db, project)
    if not state["usable"]:
        return None, state
    row = get_row(db)
    cfg = to_config(db, row)
    return ProjectSemanticSearch(project.id, cfg, row.min_semantic_score, row.semantic_weight, cfg.key), state
