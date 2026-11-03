"""Upload limits: unlimited by default (0), still enforceable when an operator sets them."""
import pytest

from app.engine.model import read_text

COBOL = "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. P{n}.\n       PROCEDURE DIVISION.\n       MAIN.\n           STOP RUN.\n"


@pytest.fixture
def project(client, auth):
    return client.post("/api/projects", json={"name": "Big upload"}, headers=auth).json()["id"]


def test_more_than_1000_files_in_one_request(client, auth, project):
    files = [("files", (f"src/P{n:04d}.cbl", COBOL.format(n=n).encode(), "text/plain")) for n in range(1200)]
    r = client.post(f"/api/projects/{project}/files", files=files, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["stage"].startswith("1200 source file(s) uploaded")


def test_limits_apply_when_configured(client, auth, project, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "max_files_per_request", 3)
    files = [("files", (f"P{n}.cbl", COBOL.format(n=n).encode(), "text/plain")) for n in range(5)]
    assert client.post(f"/api/projects/{project}/files", files=files, headers=auth).status_code == 400

    monkeypatch.setattr(settings, "max_files_per_request", 0)
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    big = [("files", ("BIG.cbl", b"      * x\n" * 200_000, "text/plain"))]  # about 2 MB
    assert client.post(f"/api/projects/{project}/files", files=big, headers=auth).status_code == 413


def test_upload_without_files_is_rejected(client, auth, project):
    r = client.post(f"/api/projects/{project}/files", data={"note": "x"}, headers=auth)
    assert r.status_code == 400


def test_large_source_files_are_read_in_full(tmp_path):
    path = tmp_path / "HUGE.cbl"
    path.write_text("      * filler line\n" * 400_000 + "       END PROGRAM HUGE.\n")  # about 8 MB
    assert read_text(path).rstrip().endswith("END PROGRAM HUGE.")


def test_document_chunks_are_not_capped_by_default():
    from app.services import doc_extract
    from app.services.doc_qa import build_chunks

    blocks = [doc_extract.Block(text=f"Paragraph {i}. " + "word " * 200) for i in range(3000)]
    chunks, truncated = build_chunks(doc_extract.Extraction(blocks=blocks), 1200, 150, 0)
    assert not truncated and len(chunks) > 800
