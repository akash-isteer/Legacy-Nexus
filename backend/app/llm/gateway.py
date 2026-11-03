"""LLM gateway: the only way the application calls an AI model.

Every request goes through the same steps:
  1. scrub     secrets and personal data are redacted from the prompt
  2. cache     identical requests (same model, prompt version and scrubbed prompt) are answered from the cache
  3. budget    monthly organisation and per-project token budgets are enforced with a hard stop
  4. route     "small" requests go to the cheap model, "large" to the main one
  5. failover  if a model fails, the next configured model is tried; a model failing repeatedly is skipped
               for a while (circuit breaker)
  6. log       one usage row per request: model, tokens, latency, prompt size and hash, sources, redactions
"""
from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.llm.providers import LLMConfig, LLMError, LLMResult, complete_json
from app.llm.scrubber import scrub
from app.models import LLMCacheEntry, LLMUsage

PROMPT_VERSION = "2026-11-02"  # bump when prompts change so old cached answers are not reused
_BREAKER_FAILURES, _BREAKER_SECONDS = 3, 60
_breaker: dict[str, tuple[int, float]] = {}
_breaker_lock = threading.Lock()


class BudgetExceeded(LLMError):
    pass


@dataclass
class GatewayResult(LLMResult):
    cached: bool = False
    redactions: int = 0
    provider: str = ""
    attempts: list[str] = field(default_factory=list)


def _model_key(cfg: LLMConfig) -> str:
    return f"{cfg.provider}:{cfg.display_model}:{cfg.base_url}"


def cache_key(cfg: LLMConfig, system: str, user: str, purpose: str) -> str:
    raw = "\x1f".join([PROMPT_VERSION, purpose, _model_key(cfg), str(cfg.temperature), system, user])
    return hashlib.sha256(raw.encode()).hexdigest()


def _open(cfg: LLMConfig) -> bool:
    with _breaker_lock:
        fails, until = _breaker.get(_model_key(cfg), (0, 0.0))
        return fails >= _BREAKER_FAILURES and time.time() < until


def _record(cfg: LLMConfig, ok: bool) -> None:
    with _breaker_lock:
        key = _model_key(cfg)
        if ok:
            _breaker.pop(key, None)
        else:
            fails = _breaker.get(key, (0, 0.0))[0] + 1
            _breaker[key] = (fails, time.time() + _BREAKER_SECONDS)


def reset_breakers() -> None:
    with _breaker_lock:
        _breaker.clear()


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def tokens_used(db: Session, project_id: int | None = None) -> int:
    q = select(func.coalesce(func.sum(LLMUsage.input_tokens + LLMUsage.output_tokens), 0)).where(
        LLMUsage.created_at >= _month_start(), LLMUsage.cached.is_(False))
    if project_id is not None:
        q = q.where(LLMUsage.project_id == project_id)
    return int(db.scalar(q) or 0)


def _check_budget(db: Session, project_id: int | None) -> None:
    from app.services.ai_config import get_row

    row = get_row(db)
    if row.monthly_token_budget and tokens_used(db) >= row.monthly_token_budget:
        raise BudgetExceeded("The monthly AI token budget is used up. An administrator can raise it in Settings → AI models.")
    if project_id is not None and row.project_token_budget and tokens_used(db, project_id) >= row.project_token_budget:
        raise BudgetExceeded("This project's monthly AI token budget is used up.")


def chain(db: Session, primary: LLMConfig, tier: str) -> list[LLMConfig]:
    """Models to try, in order: the tier's model, then the main model, then fallbacks."""
    from app.services import ai_config

    row = ai_config.get_row(db)
    models = ai_config.list_models(db)
    out: list[LLMConfig] = []
    if tier == "small":
        out += [ai_config.model_llm_config(m, row) for m in models if m.is_small]
    out.append(primary)
    out += [ai_config.model_llm_config(m, row) for m in models if m.is_fallback and not m.is_active]
    seen, unique = set(), []
    for c in out:
        if c.configured and _model_key(c) not in seen:
            seen.add(_model_key(c))
            unique.append(c)
    return unique


def complete(db: Session, cfg: LLMConfig, system: str, user: str, *, purpose: str, tier: str = "large",
             project_id: int | None = None, user_id: int | None = None, sources: list | None = None,
             use_cache: bool = True, call=None) -> GatewayResult:
    """Send one prompt through scrubbing, cache, budget, routing and failover. ``call`` overrides the
    provider function (defaults to the real HTTP call)."""
    call = call or complete_json
    clean_system, r1 = scrub(system)
    clean_user, r2 = scrub(user)
    redactions = r1 + r2
    candidates = chain(db, cfg, tier)
    if not candidates:
        raise LLMError("No AI model is configured")

    # Cache: any model in the chain that already answered this exact prompt
    if use_cache:
        for c in candidates:
            hit = db.get(LLMCacheEntry, cache_key(c, clean_system, clean_user, purpose))
            if hit is not None:
                hit.hits += 1
                hit.last_hit_at = datetime.now(timezone.utc)
                _log(db, c, purpose, tier, project_id, user_id, clean_user, redactions, sources, cached=True,
                     result=LLMResult(hit.response_text, hit.model))
                return GatewayResult(hit.response_text, hit.model, cached=True, redactions=redactions,
                                     provider=c.provider, attempts=[c.display_model])

    _check_budget(db, project_id)
    attempts, last_error = [], None
    for c in candidates:
        if _open(c) and c is not candidates[-1]:
            attempts.append(f"{c.display_model} (skipped: failing)")
            continue
        attempts.append(c.display_model)
        try:
            result = call(c, clean_system, clean_user)
        except LLMError as exc:
            _record(c, False)
            last_error = exc
            _log(db, c, purpose, tier, project_id, user_id, clean_user, redactions, sources, error=str(exc)[:500])
            continue
        _record(c, True)
        if use_cache:
            key = cache_key(c, clean_system, clean_user, purpose)
            if db.get(LLMCacheEntry, key) is None:
                db.add(LLMCacheEntry(key=key, purpose=purpose, model=result.model, response_text=result.text,
                                     input_tokens=result.input_tokens, output_tokens=result.output_tokens))
        _log(db, c, purpose, tier, project_id, user_id, clean_user, redactions, sources, result=result)
        return GatewayResult(result.text, result.model, result.input_tokens, result.output_tokens, result.latency_ms,
                             cached=False, redactions=redactions, provider=c.provider, attempts=attempts)
    raise LLMError(f"All configured models failed ({', '.join(attempts)}). Last error: {last_error}")


def _log(db: Session, cfg: LLMConfig, purpose: str, tier: str, project_id, user_id, prompt: str, redactions: int,
         sources, *, cached: bool = False, result: LLMResult | None = None, error: str | None = None) -> None:
    db.add(LLMUsage(
        project_id=project_id, user_id=user_id, purpose=purpose, provider=cfg.provider,
        model=(result.model if result else None) or cfg.display_model, tier=tier, cached=cached, ok=error is None,
        error=error, input_tokens=0 if cached or not result else result.input_tokens,
        output_tokens=0 if cached or not result else result.output_tokens,
        latency_ms=0 if not result else result.latency_ms, prompt_chars=len(prompt),
        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(), redactions=redactions, sources=sources or []))
    db.commit()


def usage_summary(db: Session, days: int = 30) -> dict:
    """Totals for the admin screen: requests, tokens, cache savings, by purpose and by model."""
    from datetime import timedelta

    from app.services.ai_config import get_row

    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(select(LLMUsage.purpose, LLMUsage.model, LLMUsage.cached, LLMUsage.ok,
                             func.count(LLMUsage.id), func.sum(LLMUsage.input_tokens), func.sum(LLMUsage.output_tokens))
                      .where(LLMUsage.created_at >= since)
                      .group_by(LLMUsage.purpose, LLMUsage.model, LLMUsage.cached, LLMUsage.ok)).all()
    total = {"requests": 0, "cached": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0}
    by_purpose: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    for purpose, model, cached, ok, n, tin, tout in rows:
        tin, tout = int(tin or 0), int(tout or 0)
        total["requests"] += n
        total["cached"] += n if cached else 0
        total["failed"] += 0 if ok else n
        total["input_tokens"] += tin
        total["output_tokens"] += tout
        for bucket, key in ((by_purpose, purpose), (by_model, model)):
            b = bucket.setdefault(key, {"requests": 0, "cached": 0, "tokens": 0})
            b["requests"] += n
            b["cached"] += n if cached else 0
            b["tokens"] += tin + tout
    row = get_row(db)
    return {"days": days, "total": total, "by_purpose": by_purpose, "by_model": by_model,
            "cache_hit_rate": round(total["cached"] / total["requests"], 3) if total["requests"] else 0.0,
            "month_tokens": tokens_used(db), "monthly_token_budget": row.monthly_token_budget,
            "project_token_budget": row.project_token_budget}
