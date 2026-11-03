"""Deleting a project removes all its data, so a new project that reuses the id starts empty."""
import io
import zipfile

from sqlalchemy import func, select

COBOL = ("       IDENTIFICATION DIVISION.\n       PROGRAM-ID. GONE.\n"
         "       PROCEDURE DIVISION.\n       MAIN.\n           IF WS-A > 1\n               STOP RUN.\n")


def _counts(pid: int) -> dict:
    from app.database import SessionLocal
    from app.models import AnalysisArtifact, BusinessRule, GraphEdge, GraphNode, SourceFile

    with SessionLocal() as db:
        return {m.__tablename__: db.scalar(select(func.count()).select_from(m).where(m.project_id == pid))
                for m in (SourceFile, AnalysisArtifact, GraphNode, GraphEdge, BusinessRule)}


def test_delete_removes_all_project_rows_and_new_project_starts_empty(client, auth):
    pid = client.post("/api/projects", json={"name": "To delete"}, headers=auth).json()["id"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("GONE.cbl", COBOL)
    assert client.post(f"/api/projects/{pid}/files", files=[("files", ("c.zip", buf.getvalue(), "application/zip"))],
                       headers=auth).status_code == 200
    assert client.post(f"/api/projects/{pid}/analyze", headers=auth).status_code == 202
    before = _counts(pid)
    assert before["source_files"] == 1 and before["analysis_artifacts"] > 0 and before["graph_nodes"] > 0

    assert client.delete(f"/api/projects/{pid}", headers=auth).status_code == 204
    assert all(n == 0 for n in _counts(pid).values()), _counts(pid)

    new = client.post("/api/projects", json={"name": "Fresh"}, headers=auth).json()["id"]
    assert client.get(f"/api/projects/{new}/files", headers=auth).json() == []
    assert all(n == 0 for n in _counts(new).values())


def test_purge_orphans_cleans_rows_of_missing_projects(client, auth):
    from app.database import SessionLocal
    from app.models import SourceFile
    from app.services.project_data import purge_orphans

    with SessionLocal() as db:
        db.add(SourceFile(project_id=987654, path="x.cbl", language="cobol", size_bytes=1, line_count=1, sha256="0" * 64))
        db.commit()
        touched = purge_orphans(db)
        db.commit()
        assert touched.get("source_files", 0) >= 1
        assert db.scalar(select(func.count()).select_from(SourceFile).where(SourceFile.project_id == 987654)) == 0
