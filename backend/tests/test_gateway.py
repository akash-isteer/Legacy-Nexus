"""LLM gateway (scrub, cache, budget, routing, failover, usage log) and graph-first answers."""
import json

import pytest

from app.engine.graph_answers import GraphAnswerer
from app.llm.providers import LLMConfig, LLMError, LLMResult
from app.llm.scrubber import scrub

# ------------------------------------------------------------------ scrubber


def test_scrubber_removes_secrets_and_personal_data():
    text = ("password = 'Sup3rSecret!'\n"
            "url: jdbc:db2://batchuser:hunter2@mainframe.bank.local:50000/BANK\n"
            "AccountKey=abcdEFGHijklMNOPqrstUVWXyz0123456789abcd==\n"
            "contact jane.doe@bank.com card 4111 1111 1111 1111 key sk-ant-abcdefghijklmnopqrstuvwxyz\n"
            "MOVE WS-AMOUNT TO WS-TOTAL")
    clean, n = scrub(text)
    for secret in ("Sup3rSecret!", "hunter2", "abcdEFGH", "jane.doe@bank.com", "4111 1111 1111 1111", "sk-ant-abc"):
        assert secret not in clean, secret
    assert "MOVE WS-AMOUNT TO WS-TOTAL" in clean and "mainframe.bank.local" in clean
    assert n >= 6


def test_scrubber_leaves_ordinary_numbers():
    clean, n = scrub("COMPUTE WS-TOTAL = 1234567890123 * 2")  # fails the card checksum
    assert n == 0 and "1234567890123" in clean


# ------------------------------------------------------------------ gateway


@pytest.fixture
def db(client):
    from app.database import SessionLocal

    with SessionLocal() as s:
        yield s


def _calls(responses):
    seen = []

    def call(cfg, system, user):
        seen.append((cfg.provider, user))
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return LLMResult(text=r, model=cfg.model, input_tokens=100, output_tokens=20, latency_ms=5)
    return call, seen


CFG = LLMConfig("anthropic", "claude-main", api_key="k" * 20)


def test_second_identical_request_is_served_from_cache(db):
    from app.llm import gateway

    call, seen = _calls(['{"answer": "one"}'])
    first = gateway.complete(db, CFG, "sys", "Where is interest calculated? password=abc12345", purpose="test", call=call)
    second = gateway.complete(db, CFG, "sys", "Where is interest calculated? password=abc12345", purpose="test", call=call)
    assert not first.cached and second.cached and second.text == '{"answer": "one"}'
    assert len(seen) == 1 and "abc12345" not in seen[0][1] and first.redactions == 1


def test_failover_to_the_next_model_and_usage_log(client, auth, db):
    from app.llm import gateway
    from app.models import LLMUsage

    fallback = client.post("/api/settings/models", json={"name": "Backup", "provider": "openai", "model": "gpt-backup",
                                                         "api_key": "sk-backup-0123456789abcdef", "is_fallback": True},
                           headers=auth).json()
    primary = client.post("/api/settings/models", json={"name": "Main", "provider": "anthropic", "model": "claude-main",
                                                        "api_key": "sk-ant-main-0123456789"}, headers=auth).json()
    client.post(f"/api/settings/models/{primary['id']}/activate", headers=auth)
    try:
        call, seen = _calls([LLMError("HTTP 529 overloaded"), '{"answer": "from backup"}'])
        r = gateway.complete(db, CFG, "sys", "a fresh question", purpose="test", call=call)
        assert r.text == '{"answer": "from backup"}' and [p for p, _ in seen] == ["anthropic", "openai"]
        rows = db.query(LLMUsage).filter(LLMUsage.purpose == "test").order_by(LLMUsage.id.desc()).limit(2).all()
        assert {row.ok for row in rows} == {True, False} and rows[0].prompt_sha256
    finally:
        for m in (fallback, primary):
            client.delete(f"/api/settings/models/{m['id']}", headers=auth)


def test_small_tier_routes_to_the_small_model(client, auth, db):
    from app.llm import gateway

    small = client.post("/api/settings/models", json={"name": "Cheap", "provider": "ollama", "model": "llama3.1:8b",
                                                      "is_small": True}, headers=auth).json()
    try:
        call, seen = _calls(['{"summary": "ok"}'])
        gateway.complete(db, CFG, "sys", "summarise this routine", purpose="summary", tier="small", call=call)
        assert seen[0][0] == "ollama"
    finally:
        client.delete(f"/api/settings/models/{small['id']}", headers=auth)


def test_budget_is_a_hard_stop(client, auth, db):
    from app.llm import gateway

    budget = gateway.tokens_used(db) + 50  # room for less than one more request
    client.put("/api/settings/ai", json={"monthly_token_budget": budget}, headers=auth)
    try:
        call, seen = _calls(['{"answer": "x"}', '{"answer": "y"}'])
        gateway.complete(db, CFG, "sys", "budget question one", purpose="test", call=call)  # spends 120 tokens
        with pytest.raises(gateway.BudgetExceeded):
            gateway.complete(db, CFG, "sys", "budget question two", purpose="test", call=call)
        usage = client.get("/api/settings/ai/usage", headers=auth).json()
        assert usage["month_tokens"] >= budget and usage["monthly_token_budget"] == budget and usage["recent"]
    finally:
        client.put("/api/settings/ai", json={"monthly_token_budget": 0}, headers=auth)


# ------------------------------------------------------------------ graph-first answers


@pytest.fixture(scope="module")
def answerer():
    from pathlib import Path

    from app.engine.pipeline import run_analysis

    a = run_analysis(Path(__file__).resolve().parent.parent / "sample_data" / "banking-core", "x")
    return GraphAnswerer(a["graph"], a["lineage"], a["insights"], a["rules"])


@pytest.mark.parametrize("question, expected", [
    ("Who calls ACCOUNT?", ["PAYMENT"]),
    ("What does ACCOUNT call?", ["CUSTOMER", "INTEREST"]),
    ("Which programs write PAYMENT_LEDGER?", ["PAYMENT"]),
    ("Which programs read the CUSTOMER_MASTER table?", ["CUSTOMER"]),
    ("Which job runs REPORT12?", ["NIGHTLY"]),
    ("What does job NIGHTLY run?", ["PAYMENT", "REPORT12"]),
    ("What tables does INTEREST read?", ["ACCOUNT_MASTER"]),
])
def test_factual_questions_come_from_the_graph(answerer, question, expected):
    r = answerer.answer(question)
    assert r is not None and r["mode"] == "graph" and r["usage"]["input_tokens"] == 0
    for name in expected:
        assert name in r["answer"], (question, r["answer"])
    assert all(c["citations"] for c in r["claims"])


def test_impact_count_and_unused_questions(answerer):
    impact = answerer.answer("What breaks if CUSTOMER_ADDRESS changes?")
    assert impact and "affects" in impact["answer"] and any("REPORT12" in c["text"] for c in impact["claims"])
    assert "6 programs" in answerer.answer("How many programs are there?")["answer"]
    unused = answerer.answer("Which programs are unused?")
    assert unused and any("LEGACYTX" in c["text"] for c in unused["claims"])


def test_explanatory_questions_go_to_retrieval(answerer):
    assert answerer.answer("Where is interest calculated?") is None
    assert answerer.answer("Why are payments held for review?") is None
    assert answerer.answer("Who calls NO_SUCH_PROGRAM?") is None


def test_ask_endpoint_uses_the_graph_first(client, auth, sample_project):
    r = client.post(f"/api/projects/{sample_project}/ask", json={"question": "Who calls ACCOUNT?"}, headers=auth).json()
    assert r["mode"] == "graph" and "PAYMENT" in r["answer"] and r["confidence"] == 1.0
    assert json.dumps(r)  # serialisable for history
