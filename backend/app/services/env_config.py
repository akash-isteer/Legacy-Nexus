"""Apply AI and embedding settings supplied by the environment (AI_* / EMBEDDING_* variables).

This is how a cloud deployment gets a working model without anyone opening Settings: infra/azure/main.bicep
passes the Azure OpenAI endpoint and deployments, with the key from Key Vault. It runs at start-up and only
fills in what has never been configured, so an administrator's later choices are never overwritten.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.embeddings import EMBEDDING_CATALOG
from app.llm.providers import PROVIDER_CATALOG
from app.services import ai_config, semantic

log = logging.getLogger("legacynexus")


def apply(db: Session) -> list[str]:
    """Returns what was applied, for the log."""
    s = get_settings()
    applied = []
    if s.ai_provider and not ai_config.list_models(db) and ai_config.get_row(db).provider in ("", "none"):
        if s.ai_provider not in PROVIDER_CATALOG:
            log.warning("AI_PROVIDER=%s is not a known provider; ignored", s.ai_provider)
        else:
            ai_config.save_model(db, {"name": "From environment", "provider": s.ai_provider, "model": s.ai_model,
                                      "deployment": s.ai_deployment, "base_url": s.ai_base_url,
                                      "api_version": s.ai_api_version, "api_key": s.ai_api_key or ""}, None)
            applied.append(f"AI model {s.ai_provider}:{s.ai_deployment or s.ai_model}")
    row = semantic.get_row(db)
    if s.embedding_provider and row.provider in ("", "none") and row.updated_by is None:
        if s.embedding_provider not in EMBEDDING_CATALOG and s.embedding_provider != "openai_compatible":
            log.warning("EMBEDDING_PROVIDER=%s is not a known provider; ignored", s.embedding_provider)
        else:
            reuse = not s.embedding_api_key and s.embedding_provider == s.ai_provider
            semantic.update(db, row, {"enabled": True, "provider": s.embedding_provider, "model": s.embedding_model,
                                      "deployment": s.embedding_deployment, "base_url": s.embedding_base_url,
                                      "api_version": s.embedding_api_version, "reuse_llm_key": reuse,
                                      "api_key": s.embedding_api_key or ""}, None)
            applied.append(f"embedding model {s.embedding_provider}:{s.embedding_deployment or s.embedding_model}")
    return applied
