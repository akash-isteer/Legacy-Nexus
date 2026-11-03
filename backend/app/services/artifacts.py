"""Persistence of analysis artifacts and a small in-process cache for the Q&A index."""
from __future__ import annotations

import threading
from collections import OrderedDict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.engine.qa import QAEngine
from app.models import AnalysisArtifact

_cache_lock = threading.Lock()
_qa_cache: OrderedDict[tuple[int, str], QAEngine] = OrderedDict()
_CACHE_SIZE = 16


def save_artifacts(db: Session, project_id: int, artifacts: dict) -> None:
    db.execute(delete(AnalysisArtifact).where(AnalysisArtifact.project_id == project_id))
    for kind, data in artifacts.items():
        db.add(AnalysisArtifact(project_id=project_id, kind=kind, data=data))
    invalidate(project_id)


def load(db: Session, project_id: int, kind: str):
    row = db.scalar(select(AnalysisArtifact).where(AnalysisArtifact.project_id == project_id, AnalysisArtifact.kind == kind))
    return row.data if row else None


def load_many(db: Session, project_id: int, kinds: list[str]) -> dict:
    rows = db.scalars(select(AnalysisArtifact).where(AnalysisArtifact.project_id == project_id,
                                                     AnalysisArtifact.kind.in_(kinds))).all()
    return {r.kind: r.data for r in rows}


def qa_engine(db: Session, project_id: int, version: str) -> QAEngine | None:
    key = (project_id, version)
    with _cache_lock:
        if key in _qa_cache:
            _qa_cache.move_to_end(key)
            return _qa_cache[key]
    data = load_many(db, project_id, ["chunks", "symbols", "graph", "lineage", "inventory"])
    if len(data) < 5:
        return None
    engine = QAEngine(data["chunks"], data["symbols"], data["graph"], data["lineage"],
                      {f["path"]: f for f in data["inventory"]})
    with _cache_lock:
        _qa_cache[key] = engine
        while len(_qa_cache) > _CACHE_SIZE:
            _qa_cache.popitem(last=False)
    return engine


def invalidate(project_id: int) -> None:
    with _cache_lock:
        for key in [k for k in _qa_cache if k[0] == project_id]:
            _qa_cache.pop(key, None)
