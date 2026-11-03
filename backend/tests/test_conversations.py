"""Context-aware Q&A and the separate Document Q&A storage.

Covers: follow-up detection and rewriting, conversations for Code Q&A and Document Q&A, history reaching the
model only for follow-ups, separate databases and vector stores, the one-time move of old document data, and
configuration of the Document Q&A database. App modules are imported inside tests (see test_doc_qa.py).
"""
import json

import pytest
from pydantic import ValidationError

# ------------------------------------------------------------------ conversation planning (pure functions)


def test_follow_up_detection():
    from app.services.conversation import is_follow_up

    for q in ("Why?", "What about CUSTOMER_MASTER?", "Who calls it?", "And the second one?", "Tell me more"):
        assert is_follow_up(q), q
    for q in ("Where is interest calculated for savings accounts?", "Which programs write PAYMENT_LEDGER?"):
        assert not is_follow_up(q), q


def test_what_about_swaps_the_subject():
    from app.services.conversation import Turn, plan

    turns = [Turn("Which programs write PAYMENT_LEDGER?", "PAYMENT writes it.", subjects=["PAYMENT_LEDGER"])]
    p = plan("What about CUSTOMER_MASTER?", turns)
    assert p.follow_up and p.resolved_question == "Which programs write CUSTOMER_MASTER?"
    assert p.retrieval_query == p.resolved_question
    assert "CONVERSATION SO FAR" in p.history_text and "PAYMENT_LEDGER" in p.history_text


def test_pronoun_is_replaced_by_previous_subject():
    from app.services.conversation import Turn, plan

    p = plan("Who calls it?", [Turn("What does ACCOUNT do?", "It posts transactions.", subjects=["ACCOUNT"])])
    assert p.resolved_question == "Who calls ACCOUNT?"


def test_standalone_question_sends_no_history_in_auto_mode():
    from app.services.conversation import Turn, plan

    turns = [Turn("What does ACCOUNT do?", "It posts transactions.", subjects=["ACCOUNT"])]
    p = plan("Where is interest calculated for savings accounts?", turns, mode="auto")
    assert not p.follow_up and p.history_text == "" and p.turns_used == 0
    assert plan("Where is interest calculated for savings accounts?", turns, mode="always").history_text
    assert plan("Why?", turns, mode="off").history_text == ""


def test_follow_up_without_subject_searches_with_previous_question():
    from app.services.conversation import Turn, plan

    p = plan("Why?", [Turn("When is the account locked?", "After three wrong passwords.")])
    assert p.resolved_question is None and "When is the account locked?" in p.retrieval_query


def test_history_is_capped():
    from app.services.conversation import Turn, history_block

    turns = [Turn(f"Question {i}?", "x " * 500) for i in range(10)]
    text = history_block(turns, 1200)
    assert len(text) < 1500 and "Question 9?" in text and "Question 0?" not in text


# ------------------------------------------------------------------ Code Q&A conversations


def test_code_follow_up_is_answered_from_the_graph(client, auth, sample_project):
    pid = sample_project
    first = client.post(f"/api/projects/{pid}/ask", json={"question": "Who calls ACCOUNT?", "new_conversation": True},
                        headers=auth).json()
    assert first["mode"] == "graph" and "PAYMENT" in first["answer"]
    cid = first["conversation_id"]
    assert first["context"]["follow_up"] is False

    second = client.post(f"/api/projects/{pid}/ask", json={"question": "What about INTEREST?", "conversation_id": cid},
                         headers=auth).json()
    assert second["conversation_id"] == cid
    assert second["context"]["follow_up"] and second["context"]["resolved_question"] == "Who calls INTEREST?"
    assert second["mode"] == "graph" and "ACCOUNT" in second["answer"]  # ACCOUNT calls INTEREST
    assert second["question"] == "What about INTEREST?"

    convo = client.get(f"/api/projects/{pid}/conversations/{cid}", headers=auth).json()
    assert [e["question"] for e in convo["entries"]] == ["Who calls ACCOUNT?", "What about INTEREST?"]
    assert convo["title"] == "Who calls ACCOUNT?"
    listed = client.get(f"/api/projects/{pid}/conversations", headers=auth).json()
    assert any(c["id"] == cid and c["turns"] == 2 for c in listed)


def test_new_conversation_starts_without_context(client, auth, sample_project):
    pid = sample_project
    client.post(f"/api/projects/{pid}/ask", json={"question": "Who calls ACCOUNT?", "new_conversation": True}, headers=auth)
    fresh = client.post(f"/api/projects/{pid}/conversations", headers=auth).json()
    r = client.post(f"/api/projects/{pid}/ask", json={"question": "What about INTEREST?", "conversation_id": fresh["id"]},
                    headers=auth).json()
    assert r["context"]["turns_used"] == 0 and r["context"]["resolved_question"] is None


def test_code_conversation_delete_and_ownership(client, auth, sample_project):
    pid = sample_project
    r = client.post(f"/api/projects/{pid}/ask", json={"question": "Who calls ACCOUNT?", "new_conversation": True},
                    headers=auth).json()
    cid = r["conversation_id"]
    assert client.post(f"/api/projects/{pid}/ask", json={"question": "Who calls ACCOUNT?", "conversation_id": 999999},
                       headers=auth).status_code == 404
    assert client.delete(f"/api/projects/{pid}/conversations/{cid}", headers=auth).status_code == 204
    assert client.get(f"/api/projects/{pid}/conversations/{cid}", headers=auth).status_code == 404
    hist = client.get(f"/api/projects/{pid}/qa/history", params={"conversation_id": cid}, headers=auth).json()
    assert hist == []


def test_code_follow_up_sends_history_to_the_model(client, auth, sample_project, monkeypatch):
    from app.llm import gateway
    from app.llm.providers import LLMResult

    prompts = []

    def fake(db, cfg, system, prompt, **kw):
        prompts.append(prompt)
        return LLMResult(text=json.dumps({"answer": "x", "not_found": True, "confidence": 0, "claims": []}),
                         model="mock", input_tokens=1, output_tokens=1, latency_ms=1)

    monkeypatch.setattr(gateway, "complete", fake)
    client.put("/api/settings/ai", json={"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": "sk-ant-test-1"},
               headers=auth)
    try:
        pid = sample_project
        a = client.post(f"/api/projects/{pid}/ask", json={"question": "Where is interest calculated?",
                                                           "new_conversation": True}, headers=auth).json()
        client.post(f"/api/projects/{pid}/ask", json={"question": "Why is it done that way?",
                                                      "conversation_id": a["conversation_id"]}, headers=auth)
    finally:
        client.put("/api/settings/ai", json={"provider": "none", "clear_api_key": True}, headers=auth)
    assert len(prompts) == 2
    assert "CONVERSATION SO FAR" not in prompts[0]
    assert "CONVERSATION SO FAR" in prompts[1] and "Where is interest calculated?" in prompts[1]


# ------------------------------------------------------------------ Document Q&A conversations

SPEC = b"""# Login
If the password is entered incorrectly three times the account is locked for 15 minutes.

# Reporting
Managers can export the monthly activity report as a CSV file of at most 10000 rows.
"""


@pytest.fixture(scope="module")
def doc_id(client, auth):
    r = client.post("/api/doc-qa/documents", headers=auth, files={"file": ("conv.md", SPEC, "text/markdown")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_document_follow_up_uses_history(client, auth, doc_id, monkeypatch):
    from app.llm.providers import LLMResult
    from app.services import doc_qa

    prompts = []

    def fake(cfg, system, user):
        prompts.append(user)
        return LLMResult(text=json.dumps({"answer": "Locked for 15 minutes.", "not_found": False, "confidence": 0.9,
                                          "citations": [{"source": 1}]}), model="mock", input_tokens=1,
                         output_tokens=1, latency_ms=1)

    monkeypatch.setattr(doc_qa, "complete_json", fake)
    client.put("/api/settings/ai", json={"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": "sk-ant-test-1"},
               headers=auth)
    try:
        a = client.post(f"/api/doc-qa/documents/{doc_id}/ask", headers=auth,
                        json={"question": "What happens after three wrong passwords?", "role": "end_user",
                              "new_conversation": True}).json()
        b = client.post(f"/api/doc-qa/documents/{doc_id}/ask", headers=auth,
                        json={"question": "For how long?", "role": "end_user",
                              "conversation_id": a["conversation_id"]}).json()
    finally:
        client.put("/api/settings/ai", json={"provider": "none", "clear_api_key": True}, headers=auth)
    assert b["context"]["follow_up"] and b["conversation_id"] == a["conversation_id"]
    assert "CONVERSATION SO FAR" not in prompts[0] and "CONVERSATION SO FAR" in prompts[1]
    assert b["evidence"]  # "For how long?" alone names nothing; the previous question drives retrieval
    convs = client.get(f"/api/doc-qa/documents/{doc_id}/conversations", headers=auth).json()
    assert convs[0]["id"] == a["conversation_id"] and convs[0]["turns"] == 2


def test_document_conversations_are_per_document(client, auth, doc_id):
    other = client.post("/api/doc-qa/documents", headers=auth,
                        files={"file": ("other.md", b"# Other\nRefunds take five days.", "text/markdown")}).json()["id"]
    a = client.post(f"/api/doc-qa/documents/{doc_id}/ask", headers=auth,
                    json={"question": "How are reports exported?", "role": "end_user", "new_conversation": True}).json()
    r = client.post(f"/api/doc-qa/documents/{other}/ask", headers=auth,
                    json={"question": "How long do refunds take?", "role": "end_user",
                          "conversation_id": a["conversation_id"]})
    assert r.status_code == 404  # a conversation belongs to its document
    client.delete(f"/api/doc-qa/documents/{other}", headers=auth)


# ------------------------------------------------------------------ separate storage


def test_document_data_lives_only_in_the_docqa_database(client, auth, doc_id):
    from sqlalchemy import inspect

    from app.database import DocSessionLocal, docqa_engine, engine
    from app.models_docqa import DocQADocument

    main_tables = set(inspect(engine).get_table_names())
    doc_tables = set(inspect(docqa_engine).get_table_names())
    assert not {"docqa_documents", "docqa_entries", "docqa_conversations"} & main_tables
    assert {"docqa_documents", "docqa_entries", "docqa_conversations"} <= doc_tables
    assert not {"projects", "users", "qa_entries", "qa_conversations"} & doc_tables
    with DocSessionLocal() as ddb:
        assert ddb.get(DocQADocument, doc_id) is not None
    assert str(engine.url) != str(docqa_engine.url)


def test_document_vectors_use_their_own_store(client, auth, monkeypatch):
    from app.config import get_settings
    from app.engine.vectors import get_store
    from app.llm.embeddings import EmbeddingConfig
    from app.services import doc_qa

    cfg = EmbeddingConfig(provider="ollama", model="fake-embed", base_url="http://x")
    monkeypatch.setattr(doc_qa.semantic, "active_config", lambda db: cfg)
    monkeypatch.setattr(doc_qa, "embed_texts", lambda c, texts, kind="document": [[1.0, float(i), 0.5] for i, _ in enumerate(texts)])
    r = client.post("/api/doc-qa/documents", headers=auth, files={"file": ("vec.md", SPEC, "text/markdown")}).json()
    assert r["embedded"] and r["search_mode"] == "hybrid"
    docs, code = get_store("docs"), get_store("code")
    assert docs is not code
    assert docs.count(r["id"]) == r["chunk_count"]
    storage = get_settings().storage_dir
    assert (storage / "docqa" / str(r["id"]) / "vectors" / "meta.json").exists()
    ask = client.post(f"/api/doc-qa/documents/{r['id']}/ask", headers=auth,
                      json={"question": "How are reports exported?", "role": "end_user"}).json()
    assert ask["retrieval"]["mode"] == "hybrid"
    client.delete(f"/api/doc-qa/documents/{r['id']}", headers=auth)
    assert docs.count(r["id"]) == 0


def test_old_document_rows_move_to_the_docqa_database(client, tmp_path):
    """A database from before this version: its documents and history are moved, then the old tables dropped."""
    from alembic import command
    from sqlalchemy import create_engine, inspect, select, text

    from app.database import DocSessionLocal, _alembic, init_db
    from app.models_docqa import DocQAConversation, DocQADocument, DocQAEntry

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    cfg = _alembic("migrations")
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0006")
    name = "legacy-move-probe.md"
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email, full_name, password_hash, role, is_active, preferences, created_at)"
                          " VALUES (1, 'old@x', 'Old', 'h', 'admin', 1, '{}', '2025-01-01')"))
        conn.execute(text(
            "INSERT INTO docqa_documents (owner_id, filename, format, size_bytes, status, char_count, chunk_count,"
            " page_count, truncated, embedded, model_key, chunks, created_at, updated_at) VALUES (1, :n, 'md', 10,"
            " 'ready', 10, 1, 0, 0, 0, '', :c, '2025-01-01 00:00:00', '2025-01-01 00:00:00')"),
            {"n": name, "c": json.dumps([{"id": "S1", "index": 0, "page": None, "section": None, "title": "excerpt",
                                          "text": "hello", "symbols": []}])})
        doc_old = conn.execute(text("SELECT id FROM docqa_documents")).scalar()
        conn.execute(text("INSERT INTO docqa_entries (document_id, user_id, question, role, response, confidence,"
                          " not_found, created_at) VALUES (:d, 1, 'Old question?', 'tester', '{\"answer\": \"a\"}',"
                          " 0.5, 0, '2025-01-02 00:00:00')"), {"d": doc_old})
    init_db(eng)
    assert "docqa_documents" not in set(inspect(eng).get_table_names())
    with DocSessionLocal() as ddb:
        doc = ddb.scalars(select(DocQADocument).where(DocQADocument.filename == name)).one()
        entries = ddb.scalars(select(DocQAEntry).where(DocQAEntry.document_id == doc.id)).all()
        assert [e.question for e in entries] == ["Old question?"]
        conv = ddb.get(DocQAConversation, entries[0].conversation_id)
        assert conv.title == "Earlier questions" and conv.document_id == doc.id


def test_readiness_reports_both_databases(client):
    report = client.get("/api/health/ready").json()
    comps = report["components"]
    assert {"database", "vectors", "docqa_database", "docqa_vectors"} <= set(comps)
    assert comps["database"]["ok"] and comps["docqa_database"]["ok"]
    assert comps["database"]["where"] != comps["docqa_database"]["where"]


# ------------------------------------------------------------------ configuration


def _settings(**kw):
    from app.config import Settings

    return Settings(_env_file=None, secret_key="x" * 48, **kw)


def test_docqa_sqlite_defaults_next_to_main_database(tmp_path):
    s = _settings(database_url=f"sqlite:///{(tmp_path / 'main.db').as_posix()}")
    assert s.docqa_database_url == f"sqlite:///{(tmp_path / 'docqa.db').as_posix()}"


def test_docqa_database_from_cloud_parts():
    s = _settings(deployment_profile="cloud", db_host="lnx-pg.postgres.database.azure.com", db_user="lnxadmin",
                  db_password="pw", db_name="legacynexus")
    assert s.docqa_database_url == ("postgresql+psycopg://lnxadmin:pw@lnx-pg.postgres.database.azure.com:5432/"
                                    "legacynexus_docqa?sslmode=require")
    other = _settings(deployment_profile="cloud", db_host="a.postgres.database.azure.com", db_user="u", db_password="p",
                      docqa_db_host="b.postgres.database.azure.com", docqa_db_name="docs")
    assert "@b.postgres.database.azure.com:5432/docs?" in other.docqa_database_url


def test_docqa_database_from_single_postgres_url():
    s = _settings(database_url="postgresql+psycopg://u:p@localhost:5432/legacynexus")
    assert s.docqa_database_url == "postgresql+psycopg://u:p@localhost:5432/legacynexus_docqa"


def test_docqa_database_must_be_separate():
    with pytest.raises(ValidationError, match="separate database"):
        _settings(database_url="sqlite:///same.db", docqa_database_url="sqlite:///same.db")
    with pytest.raises(ValidationError, match="Document Q&A"):
        _settings(deployment_profile="cloud", database_url="postgresql+psycopg://u:p@h/db",
                  docqa_database_url="sqlite:///docs.db")


def test_ai_settings_from_environment(client, monkeypatch):
    from app.config import get_settings
    from app.database import SessionLocal
    from app.models import AIModel, EmbeddingSettings
    from app.services import ai_config, env_config, semantic

    s = get_settings()
    for k, v in {"ai_provider": "azure_openai", "ai_deployment": "gpt-4o-mini", "ai_base_url": "https://x.openai.azure.com",
                 "ai_api_version": "2024-10-21", "ai_api_key": "k-123456", "embedding_provider": "azure_openai",
                 "embedding_deployment": "text-embedding-3-small", "embedding_base_url": "https://x.openai.azure.com"}.items():
        monkeypatch.setattr(s, k, v)
    with SessionLocal() as db:
        # Start from "nothing configured", as on a first cloud start.
        for m in ai_config.list_models(db):
            db.delete(m)
        ai_config.deactivate_all(db, None)
        e = semantic.get_row(db)
        saved = {f: getattr(e, f) for f in ("enabled", "provider", "deployment", "base_url", "reuse_llm_key", "updated_by")}
        e.provider, e.updated_by = "none", None
        db.commit()
        try:
            applied = env_config.apply(db)
            assert any(a.startswith("AI model azure_openai") for a in applied)
            assert any(a.startswith("embedding model azure_openai") for a in applied)
            assert ai_config.get_row(db).deployment == "gpt-4o-mini"
            assert semantic.get_row(db).enabled and semantic.get_row(db).reuse_llm_key
            assert env_config.apply(db) == []  # applied once; never overwrites what is configured
        finally:
            for m in ai_config.list_models(db):
                db.delete(m)
            ai_config.deactivate_all(db, None)
            e = db.get(EmbeddingSettings, 1)
            for f, v in saved.items():
                setattr(e, f, v)
            db.commit()
        assert db.query(AIModel).count() == 0
