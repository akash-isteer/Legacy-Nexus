"""What this deployment runs on (local or cloud), and a live check of each backing service."""
from __future__ import annotations

import tempfile

from sqlalchemy import text

from app.config import get_settings
from app.database import docqa_engine, engine


def _where(url: str) -> str:
    if url.startswith("sqlite"):
        return "local file " + (url.replace("sqlite:///", "", 1).replace("\\", "/").rsplit("/", 1)[-1] or "")
    from sqlalchemy.engine import make_url

    u = make_url(url)
    return f"{u.host or ''}/{u.database or ''}"


def _check_db(eng, url: str) -> dict:
    kind = "SQLite" if url.startswith("sqlite") else "PostgreSQL"
    where = _where(url)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True, "kind": kind, "where": where}
    except Exception as exc:  # report, never raise: this feeds a health probe
        return {"ok": False, "kind": kind, "where": where, "error": str(exc)[:200]}


def _database() -> dict:
    return _check_db(engine, get_settings().database_url)


def _docqa_database() -> dict:
    return _check_db(docqa_engine, get_settings().docqa_database_url)


def _storage() -> dict:
    s = get_settings()
    try:
        s.storage_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=s.storage_dir, prefix=".probe-", delete=True):
            pass
        return {"ok": True, "kind": "Mounted file share" if s.deployment_profile == "cloud" else "Local disk",
                "where": str(s.storage_dir)}
    except OSError as exc:
        return {"ok": False, "kind": "File storage", "where": str(s.storage_dir), "error": str(exc)[:200]}


def _jobs() -> dict:
    s = get_settings()
    if s.job_backend.lower() == "servicebus":
        configured = bool(s.servicebus_namespace or s.servicebus_connection_string)
        return {"ok": configured, "kind": "Azure Service Bus", "where": s.servicebus_namespace or "connection string",
                **({} if configured else {"error": "Set SERVICEBUS_NAMESPACE or SERVICEBUS_CONNECTION_STRING"})}
    return {"ok": True, "kind": "In-process background tasks", "where": "API process"}


def _vectors(namespace: str = "code") -> dict:
    from app.engine.vectors import NAMESPACES, PgVectorStore

    s = get_settings()
    spec = NAMESPACES[namespace]
    eng = engine if namespace == "code" else docqa_engine
    url = s.database_url if namespace == "code" else s.docqa_database_url
    folder = str(s.storage_dir / spec["folder"].split("/{id}")[0])
    mode = getattr(s, spec["setting"]).lower()
    if mode == "file" or (mode == "auto" and url.startswith("sqlite")):
        return {"ok": True, "kind": "File vector index", "where": folder}
    available = PgVectorStore.available(eng, spec["table"], spec["key"])
    if mode == "pgvector" and not available:
        return {"ok": False, "kind": "pgvector", "where": _where(url), "error": "The vector extension is not enabled"}
    return {"ok": True, "kind": "pgvector" if available else "File vector index",
            "where": f"{_where(url)} · {spec['table']}" if available else folder}


def deployment_report() -> dict:
    s = get_settings()
    return {
        "profile": s.deployment_profile,
        "environment": s.environment,
        "components": {"database": _database(), "vectors": _vectors("code"),
                       "docqa_database": _docqa_database(), "docqa_vectors": _vectors("docs"),
                       "storage": _storage(), "jobs": _jobs()},
    }
