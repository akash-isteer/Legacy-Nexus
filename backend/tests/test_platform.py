"""Platform foundation: schema migrations, knowledge-graph tables, rule review carry-over and the job queue."""
import pytest
from sqlalchemy import create_engine, inspect, select, text

def _alembic_config():
    from alembic.config import Config

    from app.database import BACKEND_DIR

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return cfg


def _head() -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_alembic_config()).get_current_head()

# ------------------------------------------------------------------ migrations


def _engine(tmp_path, name):
    return create_engine(f"sqlite:///{tmp_path / name}")


def test_fresh_database_is_migrated_to_head(client, tmp_path):
    from app.database import init_db

    eng = _engine(tmp_path, "fresh.db")
    init_db(eng)
    tables = set(inspect(eng).get_table_names())
    assert {"users", "projects", "graph_nodes", "graph_edges", "business_rules", "alembic_version"} <= tables
    with eng.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == _head()


def test_pre_migration_database_is_stamped_and_upgraded(client, tmp_path):
    from alembic import command

    from app.database import init_db

    eng = _engine(tmp_path, "legacy.db")
    cfg = _alembic_config()
    with eng.begin() as conn:  # the schema older versions created with create_all(), without a version marker
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0001")
        conn.execute(text("DROP TABLE alembic_version"))
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO users (email, full_name, password_hash, role, is_active, preferences, created_at)"
                          " VALUES ('old@x', 'Old', 'h', 'admin', 1, '{}', '2025-01-01')"))
    init_db(eng)
    tables = set(inspect(eng).get_table_names())
    assert {"graph_nodes", "business_rules", "alembic_version"} <= tables
    with eng.connect() as conn:  # existing data survives
        assert conn.execute(text("SELECT email FROM users")).scalar() == "old@x"


# ------------------------------------------------------------------ knowledge graph tables


def test_graph_tables_answer_queries(client, auth, sample_project):
    pid = sample_project
    programs = client.get(f"/api/projects/{pid}/graph/search", params={"types": "program"}, headers=auth).json()
    assert {p["label"] for p in programs} >= {"CUSTOMER", "ACCOUNT", "PAYMENT", "INTEREST", "REPORT12", "LEGACYTX"}
    found = client.get(f"/api/projects/{pid}/graph/search", params={"q": "ledger"}, headers=auth).json()
    assert any(n["id"] == "table:PAYMENT_LEDGER" for n in found)

    n = client.get(f"/api/projects/{pid}/graph/neighbours", params={"node": "program:ACCOUNT"}, headers=auth).json()
    calls_in = [e for e in n["edges"] if e["type"] == "CALLS" and e["direction"] == "in"]
    calls_out = {e["target"] for e in n["edges"] if e["type"] == "CALLS" and e["direction"] == "out"}
    assert [e["source"] for e in calls_in] == ["program:PAYMENT"] and calls_in[0]["evidence"]
    assert {"program:CUSTOMER", "program:INTEREST"} <= calls_out
    missing = client.get(f"/api/projects/{pid}/graph/neighbours", params={"node": "program:NOPE"}, headers=auth)
    assert missing.status_code == 404


def test_rule_review_survives_reanalysis(client, auth, sample_project):
    from app.database import SessionLocal
    from app.models import BusinessRule
    from app.services import artifacts, graph_store

    with SessionLocal() as db:
        before = {r.fingerprint: r.review_status for r in
                  db.scalars(select(BusinessRule).where(BusinessRule.project_id == sample_project))}
        rule = db.scalars(select(BusinessRule).where(BusinessRule.project_id == sample_project,
                                                     BusinessRule.is_technical.is_(False),
                                                     BusinessRule.review_status == "candidate")).first()
        rule.review_status, rule.review_note = "accepted", "Confirmed with the payments team"
        fingerprint = rule.fingerprint
        db.commit()
        data = artifacts.load_many(db, sample_project, ["graph", "rules"])
        graph_store.save(db, sample_project, data["graph"], data["rules"])  # what a re-analysis does
        db.commit()
        again = db.scalars(select(BusinessRule).where(BusinessRule.project_id == sample_project,
                                                      BusinessRule.fingerprint == fingerprint)).one()
        assert again.review_status == "accepted" and again.review_note == "Confirmed with the payments team"
        others = db.scalars(select(BusinessRule).where(BusinessRule.project_id == sample_project,
                                                       BusinessRule.fingerprint != fingerprint)).all()
        assert others and all(r.review_status == before[r.fingerprint] for r in others)  # others untouched


def test_rule_fingerprint_ignores_numbering_and_wording():
    from app.services.graph_store import rule_fingerprint

    base = {"program": "ACCOUNT", "kind": "decision", "paragraph": "3000-POST-TRANSACTION",
            "condition": "WS-NEW-BALANCE < ZERO", "actions": ["SUBTRACT WS-OVERDRAFT-FEE FROM WS-NEW-BALANCE"],
            "fields": ["WS-NEW-BALANCE"]}
    renumbered = {**base, "id": "BR-099", "description": "reworded", "start_line": 90}
    assert rule_fingerprint(base) == rule_fingerprint(renumbered)
    assert rule_fingerprint(base) != rule_fingerprint({**base, "condition": "WS-NEW-BALANCE < 100"})


# ------------------------------------------------------------------ job queue


@pytest.fixture
def servicebus_mode(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "job_backend", "servicebus")


def test_servicebus_backend_queues_instead_of_running(client, auth, servicebus_mode, monkeypatch):
    from app.services import jobs

    sent = []
    monkeypatch.setattr(jobs, "_send_servicebus", sent.append)
    r = client.post("/api/projects/sample", headers=auth)
    assert r.status_code == 201
    pid = r.json()["id"]
    assert sent == [{"kind": "analyze", "project_id": pid}]
    project = client.get(f"/api/projects/{pid}", headers=auth).json()
    assert project["status"] == "analyzing" and project["stage"] == "Queued"  # waiting for a worker
    assert client.post(f"/api/projects/{pid}/analyze", headers=auth).status_code == 409  # already queued

    jobs.handle(sent[0])  # what a worker does with the message
    assert client.get(f"/api/projects/{pid}", headers=auth).json()["status"] == "ready"


def test_queue_outage_fails_the_request_clearly(client, auth, servicebus_mode, monkeypatch):
    from app.services import jobs

    def down(body):
        raise jobs.JobSubmitError("The job queue is unavailable; try again shortly")

    monkeypatch.setattr(jobs, "_send_servicebus", down)
    r = client.post("/api/projects/sample", headers=auth)
    assert r.status_code == 503
    latest = max(client.get("/api/projects", headers=auth).json(), key=lambda p: p["id"])
    assert latest["status"] == "failed" and "unavailable" in latest["error"]


def test_malformed_job_messages_are_rejected():
    from app.services import jobs

    for body in ({"kind": "delete_everything", "project_id": 1}, {"kind": "analyze", "project_id": "1"}, {}):
        with pytest.raises(ValueError):
            jobs.handle(body)


class FakeReceiver:
    def __init__(self):
        self.calls = []

    def complete_message(self, m):
        self.calls.append("complete")

    def abandon_message(self, m):
        self.calls.append("abandon")

    def dead_letter_message(self, m, reason, error_description):
        self.calls.append(f"dead-letter:{reason}")


def test_worker_settles_messages(monkeypatch):
    from app import worker
    from app.services import jobs

    handled = []
    monkeypatch.setattr(jobs, "handle", handled.append)
    rec = FakeReceiver()
    worker.process(rec, "not json")
    worker.process(rec, '{"kind": "analyze", "project_id": 7}')
    assert handled == [{"kind": "analyze", "project_id": 7}]

    def boom(body):
        raise RuntimeError("database restarting")

    monkeypatch.setattr(jobs, "handle", boom)
    worker.process(rec, '{"kind": "analyze", "project_id": 7}')
    assert rec.calls == ["dead-letter:malformed", "complete", "abandon"]
