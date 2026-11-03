import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    os.environ.update({
        "SECRET_KEY": "test-secret-key-0123456789-abcdefghijklmnop",
        "DATABASE_URL": f"sqlite:///{data / 'test.db'}",
        "STORAGE_DIR": str(data / "storage"),
        "BOOTSTRAP_ADMIN_EMAIL": "admin@test.local",
        "BOOTSTRAP_ADMIN_PASSWORD": "Test@12345",
    })
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(autouse=True)
def _fresh_llm_cache(request):
    """Each test sees an empty AI response cache, so one test's mocked answer cannot leak into another."""
    yield
    if "app.database" in sys.modules:
        from sqlalchemy import text

        from app.database import SessionLocal
        from app.llm.gateway import reset_breakers

        try:
            with SessionLocal() as db:
                db.execute(text("DELETE FROM llm_cache"))
                db.commit()
        except Exception:
            pass  # database not initialised in pure unit tests
        reset_breakers()


@pytest.fixture(scope="session")
def auth(client):
    r = client.post("/api/auth/login", json={"email": "admin@test.local", "password": "Test@12345"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def sample_project(client, auth):
    r = client.post("/api/projects/sample", headers=auth)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    # TestClient runs background tasks before returning, so the analysis is complete here.
    project = client.get(f"/api/projects/{pid}", headers=auth).json()
    assert project["status"] == "ready", project
    return pid
