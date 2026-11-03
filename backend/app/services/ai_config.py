"""Organisation AI settings: the saved model library, the active model, and Q&A tuning."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.engine.qa import QASettings
from app.llm.providers import PROVIDER_CATALOG, LLMConfig
from app.models import AIModel, AISettings
from app.security import decrypt_secret, encrypt_secret, mask_secret


def get_row(db: Session) -> AISettings:
    row = db.get(AISettings, 1)
    if row is None:
        row = AISettings(id=1)
        db.add(row)
        db.commit()
    return row


def to_public(row: AISettings) -> dict:
    settings = get_settings()
    key = decrypt_secret(row.encrypted_api_key)
    cfg = to_llm_config(row)
    return {
        "provider": row.provider,
        "model": row.model,
        "base_url": row.base_url,
        "api_version": row.api_version,
        "deployment": row.deployment,
        "api_key_set": bool(key),
        "api_key_masked": mask_secret(key),
        "temperature": row.temperature,
        "max_output_tokens": row.max_output_tokens,
        "top_k": row.top_k or settings.qa_top_k,
        "context_char_budget": row.context_char_budget or settings.qa_context_char_budget,
        "min_retrieval_score": row.min_retrieval_score if row.min_retrieval_score is not None else settings.qa_min_retrieval_score,
        "configured": cfg.configured,
        "monthly_token_budget": row.monthly_token_budget,
        "project_token_budget": row.project_token_budget,
        "updated_at": row.updated_at,
    }


def update(db: Session, row: AISettings, data: dict, user_id: int) -> AISettings:
    if "provider" in data and data["provider"] not in PROVIDER_CATALOG and data["provider"] != "none":
        raise ValueError(f"Unknown provider {data['provider']}")
    for field in ("provider", "model", "base_url", "api_version", "deployment", "temperature", "max_output_tokens",
                  "top_k", "context_char_budget", "min_retrieval_score"):
        if field in data and data[field] is not None:
            setattr(row, field, data[field].strip() if isinstance(data[field], str) else data[field])
    for field in ("monthly_token_budget", "project_token_budget"):
        if field in data and data[field] is not None:
            setattr(row, field, data[field] or None)  # 0 removes the cap
    if data.get("clear_api_key"):
        row.encrypted_api_key = ""
    elif data.get("api_key"):
        row.encrypted_api_key = encrypt_secret(data["api_key"].strip())
    row.updated_by = user_id
    db.commit()
    return row


def to_llm_config(row: AISettings) -> LLMConfig:
    return LLMConfig(
        provider=row.provider,
        model=row.model,
        api_key=decrypt_secret(row.encrypted_api_key),
        base_url=row.base_url,
        api_version=row.api_version,
        deployment=row.deployment,
        temperature=row.temperature,
        max_output_tokens=row.max_output_tokens,
        timeout_seconds=get_settings().llm_request_timeout_seconds,
    )


# ----------------------------------------------------------------------------- saved model library

MODEL_FIELDS = ("provider", "model", "base_url", "api_version", "deployment")


def list_models(db: Session) -> list[AIModel]:
    return list(db.scalars(select(AIModel).order_by(AIModel.is_active.desc(), AIModel.name)))


def model_public(m: AIModel) -> dict:
    key = decrypt_secret(m.encrypted_api_key)
    catalog = PROVIDER_CATALOG.get(m.provider, {})
    return {
        "id": m.id, "name": m.name, "provider": m.provider, "provider_label": catalog.get("label", m.provider),
        "model": m.model, "base_url": m.base_url, "api_version": m.api_version, "deployment": m.deployment,
        "api_key_set": bool(key), "api_key_masked": mask_secret(key), "is_active": m.is_active,
        "is_small": bool(m.is_small), "is_fallback": bool(m.is_fallback),
        "configured": _llm_config_for(m).configured, "last_test_ok": m.last_test_ok,
        "last_tested_at": m.last_tested_at, "created_at": m.created_at, "updated_at": m.updated_at,
    }


def save_model(db: Session, data: dict, user_id: int, model: AIModel | None = None) -> AIModel:
    provider = data.get("provider", model.provider if model else None)
    if provider not in PROVIDER_CATALOG:
        raise ValueError(f"Unknown provider {provider}")
    m = model or AIModel(created_by=user_id, provider=provider)
    for f in MODEL_FIELDS:
        if f in data and data[f] is not None:
            setattr(m, f, data[f].strip())
    if data.get("name"):
        m.name = data["name"].strip()
    if not m.name:
        m.name = f"{PROVIDER_CATALOG[provider]['label'].split(' (')[0]} · {m.model or m.deployment}".strip(" ·")
    if data.get("clear_api_key"):
        m.encrypted_api_key = ""
    elif data.get("api_key"):
        m.encrypted_api_key = encrypt_secret(data["api_key"].strip())
    for flag in ("is_small", "is_fallback"):
        if data.get(flag) is not None:
            setattr(m, flag, bool(data[flag]))
    if model is None:
        db.add(m)
    db.flush()
    if m.is_active or (model is None and not any(x.is_active for x in list_models(db) if x is not m)):
        activate_model(db, m, user_id)  # the first model saved becomes the active one
    db.commit()
    return m


def activate_model(db: Session, m: AIModel, user_id: int) -> None:
    for other in list_models(db):
        other.is_active = other.id == m.id
    row = get_row(db)
    for f in MODEL_FIELDS:
        setattr(row, f, getattr(m, f))
    row.encrypted_api_key = m.encrypted_api_key
    row.updated_by = user_id
    db.commit()


def delete_model(db: Session, m: AIModel, user_id: int) -> None:
    was_active = m.is_active
    db.delete(m)
    db.flush()
    if was_active:  # no model is active any more: Q&A falls back to evidence-only answers
        row = get_row(db)
        row.provider, row.model, row.base_url, row.api_version, row.deployment = "none", "", "", "", ""
        row.encrypted_api_key = ""
        row.updated_by = user_id
    db.commit()


def deactivate_all(db: Session, user_id: int) -> None:
    for m in list_models(db):
        m.is_active = False
    row = get_row(db)
    row.provider = "none"
    row.updated_by = user_id
    db.commit()


def sync_library_from_settings(db: Session, row: AISettings, user_id: int) -> None:
    """Keep the library in step when the active configuration is edited directly (PUT /settings/ai)."""
    if row.provider in ("none", ""):
        for m in list_models(db):
            m.is_active = False
        db.commit()
        return
    active = next((m for m in list_models(db) if m.is_active), None)
    if active is None or active.provider != row.provider:
        active = AIModel(created_by=user_id, provider=row.provider, name="")
        db.add(active)
    for f in MODEL_FIELDS:
        setattr(active, f, getattr(row, f))
    active.encrypted_api_key = row.encrypted_api_key
    if not active.name:
        active.name = f"{PROVIDER_CATALOG[row.provider]['label'].split(' (')[0]} · {row.model or row.deployment}".strip(" ·")
    db.flush()
    for m in list_models(db):
        m.is_active = m.id == active.id
    db.commit()


def _llm_config_for(m: AIModel) -> LLMConfig:
    return LLMConfig(provider=m.provider, model=m.model, api_key=decrypt_secret(m.encrypted_api_key),
                     base_url=m.base_url, api_version=m.api_version, deployment=m.deployment,
                     timeout_seconds=get_settings().llm_request_timeout_seconds)


def model_llm_config(m: AIModel, row: AISettings) -> LLMConfig:
    cfg = _llm_config_for(m)
    cfg.temperature, cfg.max_output_tokens = row.temperature, row.max_output_tokens
    return cfg


def to_qa_settings(row: AISettings) -> QASettings:
    s = get_settings()
    return QASettings(
        top_k=row.top_k or s.qa_top_k,
        context_char_budget=row.context_char_budget or s.qa_context_char_budget,
        min_retrieval_score=row.min_retrieval_score if row.min_retrieval_score is not None else s.qa_min_retrieval_score,
    )
