from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.deps import DB, AdminUser, CurrentUser
from app.engine.qa import parse_json
from app.llm.embeddings import EMBEDDING_CATALOG, EmbeddingError, embed_texts
from app.llm.providers import PROVIDER_CATALOG, LLMError, complete_json
from app.llm import gateway
from app.models import AIModel, AuditLog, LLMUsage, User
from app.schemas import AIModelIn, AISettingsUpdate, EmbeddingSettingsUpdate
from app.services import ai_config, audit, semantic

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/providers")
def providers(_: CurrentUser) -> dict:
    return PROVIDER_CATALOG


@router.get("/ai")
def get_ai(_: CurrentUser, db: DB) -> dict:
    return ai_config.to_public(ai_config.get_row(db))


@router.put("/ai")
def update_ai(body: AISettingsUpdate, admin: AdminUser, db: DB, request: Request) -> dict:
    row = ai_config.get_row(db)
    try:
        ai_config.update(db, row, body.model_dump(exclude_unset=True), admin.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    if {"provider", "model", "base_url", "api_version", "deployment", "api_key", "clear_api_key"} & body.model_fields_set:
        ai_config.sync_library_from_settings(db, row, admin.id)
    changed = sorted(k for k in body.model_dump(exclude_unset=True) if k != "api_key")
    if body.api_key:
        changed.append("api_key(rotated)")
    audit.record(db, "ai_settings_updated", admin.id, "ai_settings", {"fields": changed, "provider": row.provider}, request)
    return ai_config.to_public(row)


# ----------------------------------------------------------------------------- saved models


def _model(db, model_id: int) -> AIModel:
    m = db.get(AIModel, model_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Model not found")
    return m


@router.get("/models")
def list_models(_: CurrentUser, db: DB) -> list[dict]:
    """Every saved AI model connection; the active one answers Q&A questions."""
    return [ai_config.model_public(m) for m in ai_config.list_models(db)]


@router.post("/models", status_code=status.HTTP_201_CREATED)
def add_model(body: AIModelIn, admin: AdminUser, db: DB, request: Request) -> dict:
    try:
        m = ai_config.save_model(db, body.model_dump(exclude_unset=True), admin.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    audit.record(db, "ai_model_added", admin.id, f"ai_model:{m.id}", {"provider": m.provider, "model": m.model}, request)
    return ai_config.model_public(m)


@router.put("/models/{model_id}")
def edit_model(model_id: int, body: AIModelIn, admin: AdminUser, db: DB, request: Request) -> dict:
    m = _model(db, model_id)
    try:
        ai_config.save_model(db, body.model_dump(exclude_unset=True), admin.id, m)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    audit.record(db, "ai_model_updated", admin.id, f"ai_model:{m.id}", {"provider": m.provider}, request)
    return ai_config.model_public(m)


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_model(model_id: int, admin: AdminUser, db: DB, request: Request) -> None:
    m = _model(db, model_id)
    detail = {"provider": m.provider, "model": m.model, "was_active": m.is_active}
    ai_config.delete_model(db, m, admin.id)
    audit.record(db, "ai_model_deleted", admin.id, f"ai_model:{model_id}", detail, request)


@router.post("/models/{model_id}/activate")
def activate_model(model_id: int, admin: AdminUser, db: DB, request: Request) -> dict:
    m = _model(db, model_id)
    ai_config.activate_model(db, m, admin.id)
    audit.record(db, "ai_model_activated", admin.id, f"ai_model:{m.id}", {"provider": m.provider, "model": m.model}, request)
    return ai_config.model_public(m)


@router.post("/models/deactivate")
def deactivate_models(admin: AdminUser, db: DB, request: Request) -> dict:
    """Turn AI answers off: Q&A returns cited evidence only, with no model calls."""
    ai_config.deactivate_all(db, admin.id)
    audit.record(db, "ai_models_deactivated", admin.id, "ai_settings", request=request)
    return {"ok": True}


@router.post("/models/{model_id}/test")
def test_model(model_id: int, admin: AdminUser, db: DB, request: Request) -> dict:
    m = _model(db, model_id)
    cfg = ai_config.model_llm_config(m, ai_config.get_row(db))
    if not cfg.configured:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Fill in the required fields for this provider first")
    result = _ping(cfg)
    m.last_test_ok, m.last_tested_at = result["ok"], datetime.now(timezone.utc)
    db.commit()
    audit.record(db, "ai_model_tested", admin.id, f"ai_model:{m.id}", {"ok": result["ok"]}, request)
    return result


@router.get("/ai/usage")
def ai_usage(_: AdminUser, db: DB, days: int = Query(30, ge=1, le=365), limit: int = Query(25, le=200)) -> dict:
    """AI spend: totals, cache savings, budgets, and the most recent requests (what was sent to which model)."""
    summary = gateway.usage_summary(db, days)
    recent = db.execute(select(LLMUsage, User.email).join(User, User.id == LLMUsage.user_id, isouter=True)
                        .order_by(LLMUsage.created_at.desc()).limit(limit)).all()
    summary["recent"] = [{
        "id": u.id, "created_at": u.created_at, "purpose": u.purpose, "provider": u.provider, "model": u.model,
        "tier": u.tier, "cached": u.cached, "ok": u.ok, "error": u.error, "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens, "latency_ms": u.latency_ms, "prompt_chars": u.prompt_chars,
        "prompt_sha256": u.prompt_sha256, "redactions": u.redactions, "sources": u.sources[:10],
        "project_id": u.project_id, "user": email} for u, email in recent]
    return summary


def _ping(cfg) -> dict:
    try:
        result = complete_json(cfg, "You are a connectivity check. Reply with JSON only.",
                               'Return exactly this JSON object: {"status": "ok"}')
    except LLMError as exc:
        return {"ok": False, "message": str(exc)}
    parsed = parse_json(result.text)
    ok = bool(parsed and str(parsed.get("status", "")).lower() == "ok")
    return {"ok": ok, "model": result.model, "latency_ms": result.latency_ms,
            "message": "Connected. The model returned valid JSON." if ok else "Connected, but the model did not return the expected JSON."}


@router.post("/ai/test")
def test_ai(admin: AdminUser, db: DB, request: Request) -> dict:
    cfg = ai_config.to_llm_config(ai_config.get_row(db))
    if not cfg.configured:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a provider and fill in the required fields first")
    try:
        result = complete_json(cfg, "You are a connectivity check. Reply with JSON only.",
                               'Return exactly this JSON object: {"status": "ok"}')
    except LLMError as exc:
        audit.record(db, "ai_test_failed", admin.id, "ai_settings", {"error": str(exc)[:300]}, request)
        return {"ok": False, "message": str(exc)}
    parsed = parse_json(result.text)
    ok = bool(parsed and str(parsed.get("status", "")).lower() == "ok")
    audit.record(db, "ai_test", admin.id, "ai_settings", {"ok": ok, "model": result.model}, request)
    return {"ok": ok, "model": result.model, "latency_ms": result.latency_ms,
            "message": "Connected. The model returned valid JSON." if ok else "Connected, but the model did not return the expected JSON."}


@router.get("/embedding-providers")
def embedding_providers(_: CurrentUser) -> dict:
    return EMBEDDING_CATALOG


@router.get("/embeddings")
def get_embeddings(_: CurrentUser, db: DB) -> dict:
    return semantic.to_public(db, semantic.get_row(db))


@router.put("/embeddings")
def update_embeddings(body: EmbeddingSettingsUpdate, admin: AdminUser, db: DB, request: Request) -> dict:
    row = semantic.get_row(db)
    try:
        semantic.update(db, row, body.model_dump(exclude_unset=True), admin.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    changed = sorted(k for k in body.model_dump(exclude_unset=True) if k != "api_key")
    if body.api_key:
        changed.append("api_key(rotated)")
    audit.record(db, "embedding_settings_updated", admin.id, "embedding_settings",
                 {"fields": changed, "provider": row.provider}, request)
    return semantic.to_public(db, row)


@router.post("/embeddings/test")
def test_embeddings(admin: AdminUser, db: DB, request: Request) -> dict:
    cfg = semantic.to_config(db, semantic.get_row(db))
    if not cfg.configured:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose an embedding provider and fill in the required fields first")
    import time

    started = time.perf_counter()
    try:
        vectors = embed_texts(cfg, ["COMPUTE WS-INTEREST = WS-BALANCE * WS-RATE"], kind="query")
    except EmbeddingError as exc:
        audit.record(db, "embedding_test_failed", admin.id, "embedding_settings", {"error": str(exc)[:300]}, request)
        return {"ok": False, "message": str(exc)}
    latency = int((time.perf_counter() - started) * 1000)
    audit.record(db, "embedding_test", admin.id, "embedding_settings", {"dim": len(vectors[0])}, request)
    return {"ok": True, "dim": len(vectors[0]), "latency_ms": latency,
            "message": f"Connected. {cfg.key} returns {len(vectors[0])}-dimension vectors."}


@router.get("/audit")
def audit_log(_: AdminUser, db: DB, limit: int = Query(50, le=200), offset: int = 0, action: str | None = None) -> dict:
    q = select(AuditLog, User.email).join(User, User.id == AuditLog.user_id, isouter=True)
    count_q = select(func.count(AuditLog.id))
    if action:
        q = q.where(AuditLog.action == action)
        count_q = count_q.where(AuditLog.action == action)
    rows = db.execute(q.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)).all()
    return {
        "total": db.scalar(count_q),
        "items": [{"id": a.id, "action": a.action, "target": a.target, "detail": a.detail, "ip_address": a.ip_address,
                   "user": email, "created_at": a.created_at} for a, email in rows],
    }
