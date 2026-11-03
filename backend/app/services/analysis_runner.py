"""Runs an analysis in the background and records progress on the project row.

It only needs a project id and creates its own database session, so it runs the same way in the API
process (JOB_BACKEND=inline) or in a queue worker (JOB_BACKEND=servicebus, see app/worker.py).
"""
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import delete

from app.database import SessionLocal
from app.engine.api_surface import diff_surfaces
from app.engine.overview_extras import diff_snapshots, snapshot
from app.engine.parse_cache import FileParseCache
from app.engine.pipeline import run_analysis
from app.models import Project, SourceFile
from app.services import artifacts, graph_store, semantic
from app.services.storage import parse_cache_dir, project_dir

log = logging.getLogger("legacynexus.analysis")
_running: set[int] = set()
_lock = threading.Lock()


def is_running(project_id: int) -> bool:
    with _lock:
        return project_id in _running


def is_busy(project: Project) -> bool:
    """True while an analysis is queued or running, in this process or on a queue worker."""
    return is_running(project.id) or project.status == "analyzing"


def analyze_project(project_id: int) -> None:
    with _lock:
        if project_id in _running:
            return
        _running.add(project_id)
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None:
            return

        def progress(pct: int, stage: str) -> None:
            project.progress, project.stage = pct, stage
            db.commit()

        project.status, project.error = "analyzing", None
        progress(1, "Starting analysis")
        cache = FileParseCache(parse_cache_dir(project_id))
        result = run_analysis(project_dir(project_id), project.name, progress, cache)

        previous = artifacts.load(db, project_id, "api_surface")  # last analysis, before it is replaced
        if previous and previous.get("operations") is not None:
            changes = diff_surfaces(previous["operations"], result["api_surface"]["operations"])
            changes["compared_to"] = project.analyzed_at.isoformat() if project.analyzed_at else None
        else:
            changes = {"baseline": True, "breaking": [], "non_breaking": [], "summary": {"breaking": 0, "non_breaking": 0}}
        result["api_surface"]["changes"] = changes
        result["insights"]["overview"]["api_breaking_changes"] = changes["summary"]["breaking"]

        # What changed since the last run (files, components, business rules), for the Overview.
        current = snapshot(result["inventory"], result["graph"], result["rules"], graph_store.rule_fingerprint)
        result["snapshot"] = current
        result["insights"]["changes"] = dict(diff_snapshots(_previous_snapshot(db, project_id), current),
                                             compared_to=project.analyzed_at.isoformat() if project.analyzed_at else None)

        progress(97, "Saving results")
        db.execute(delete(SourceFile).where(SourceFile.project_id == project_id))
        for f in result["inventory"]:
            db.add(SourceFile(project_id=project_id, path=f["path"], language=f["language"],
                              size_bytes=f["size_bytes"], line_count=f["line_count"], sha256=f["sha256"]))
        artifacts.save_artifacts(db, project_id, result)
        graph_store.save(db, project_id, result["graph"], result["rules"])
        project.stats = result["insights"]["overview"]
        project.status, project.progress, project.stage = "ready", 100, "Analysis complete"
        project.analyzed_at = datetime.now(timezone.utc)
        db.commit()
        log.info("Project %s analysed: %s", project_id, project.stats)
        auto_semantic = _wants_semantic_index(db)
    except Exception as exc:  # report any failure on the project instead of losing it in a worker
        log.exception("Analysis failed for project %s", project_id)
        db.rollback()
        project = db.get(Project, project_id)
        if project:
            project.status, project.error, project.stage = "failed", str(exc)[:2000], "Analysis failed"
            db.commit()
    else:
        if auto_semantic:  # index for hybrid search once the analysis is published
            semantic.build_index(project_id)
    finally:
        db.close()
        with _lock:
            _running.discard(project_id)


def _previous_snapshot(db, project_id: int) -> dict | None:
    """The last run's snapshot. Runs made before snapshots existed are rebuilt from their saved results."""
    saved = artifacts.load(db, project_id, "snapshot")
    if saved:
        return saved
    old = artifacts.load_many(db, project_id, ["graph", "rules"])
    if "graph" not in old:
        return None
    files = [{"path": f.path, "sha256": f.sha256}
             for f in db.query(SourceFile).filter(SourceFile.project_id == project_id)]
    return snapshot(files, old["graph"], old.get("rules") or [], graph_store.rule_fingerprint)


def _wants_semantic_index(db) -> bool:
    try:
        return semantic.get_row(db).auto_index and semantic.active_config(db) is not None
    except Exception:
        return False
