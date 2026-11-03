"""Hybrid (keyword + semantic) search: fusion, gating, degradation, providers and vector stores."""
import json
import os
import re
import zlib
from pathlib import Path

import httpx
import numpy as np
import pytest

from app.engine.pipeline import run_analysis
from app.engine.qa import QAEngine, QASettings
from app.engine.vectors import FileVectorStore
from app.llm import embeddings
from app.llm.embeddings import EmbeddingConfig, embed_texts

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "banking-core"

# A tiny "concept" embedder standing in for a real model: words that mean the same thing share a dimension.
CONCEPTS = [
    {"overdraft", "negative", "red", "penalty", "penalised", "penalized", "fee"},
    {"interest", "rate", "accrue", "accrued"},
    {"customer", "client", "address", "mailing", "holder"},
    {"payment", "payments", "ledger", "posted", "posting"},
    {"report", "statement", "statements"},
]


def concept_vector(text: str) -> list[float]:
    words = re.findall(r"[a-z]+", text.lower())
    vec = np.zeros(len(CONCEPTS) + 64, dtype=np.float32)
    for w in words:
        for i, group in enumerate(CONCEPTS):
            if w in group:
                vec[i] += 1.0
    if not vec.any():  # unrelated text points in its own direction, like a real model's "other" topics
        vec[len(CONCEPTS) + zlib.crc32(text.encode()) % 64] = 1.0
    return (vec / np.linalg.norm(vec)).tolist()


@pytest.fixture(scope="module")
def analysis():
    return run_analysis(SAMPLE, "Core Banking")


@pytest.fixture(scope="module")
def engine(analysis):
    return QAEngine(analysis["chunks"], analysis["symbols"], analysis["graph"], analysis["lineage"],
                    {f["path"]: f for f in analysis["inventory"]})


class StubSemantic:
    """Semantic searcher over the concept embedder, in memory."""

    min_score, weight, label = 0.35, 1.0, "stub"

    def __init__(self, chunks):
        self.ids = [c["id"] for c in chunks]
        self.matrix = np.array([concept_vector(f"{c['title']} {c['text']}") for c in chunks])

    def search(self, question, top_k):
        sims = self.matrix @ np.array(concept_vector(question))
        order = np.argsort(-sims)[:top_k]
        return [(self.ids[i], float(sims[i])) for i in order]


QUESTION = "Where are people penalised for going into the red?"  # no keyword overlap with the code


def test_keyword_only_misses_paraphrase(engine):
    r = engine.answer(QUESTION, QASettings(), None)
    assert r["not_found"] and r["retrieval"]["mode"] == "keyword"


def test_hybrid_finds_paraphrase(engine, analysis):
    r = engine.answer(QUESTION, QASettings(), None, semantic=StubSemantic(analysis["chunks"]))
    assert not r["not_found"] and r["retrieval"]["mode"] == "hybrid"
    top = r["evidence"][0]
    assert top["file"] == "cobol/ACCOUNT.CBL" and "3000-POST-TRANSACTION" in top["title"]
    assert top["found_by"] == "semantic" and top["similarity"] > 0.35


def test_hybrid_keeps_exact_name_matches(engine, analysis):
    r = engine.retrieve("Which programs use CUSTOMER_ADDRESS?", QASettings(), StubSemantic(analysis["chunks"]))
    assert r.evidence[0]["found_by"] == "both" and r.evidence[0]["program"] == "CUSTOMER"


def test_semantic_failure_degrades_to_keyword(engine):
    class Broken:
        min_score, weight, label = 0.35, 1.0, "broken"

        def search(self, q, k):
            raise RuntimeError("embedding service down")

    r = engine.answer("Where is interest calculated?", QASettings(), None, semantic=Broken())
    assert r["retrieval"]["mode"] == "keyword" and "Semantic search unavailable" in r["warning"]
    assert r["evidence"]


def test_unrelated_question_still_abstains(engine, analysis):
    r = engine.answer("What is the capital of France?", QASettings(), None, semantic=StubSemantic(analysis["chunks"]))
    assert r["not_found"]


def test_file_vector_store_roundtrip(tmp_path):
    store = FileVectorStore(tmp_path)
    store.save(7, "m:1", ["a", "b", "c"], [[1, 0, 0], [0, 1, 0], [0.7, 0.7, 0]])
    hits = store.search(7, "m:1", [1, 0.1, 0], 2)
    assert [h[0] for h in hits] == ["a", "c"] and hits[0][1] > 0.99
    assert store.search(7, "other-model", [1, 0, 0], 2) == []  # different model: never mixed


@pytest.mark.skipif(not os.getenv("LNX_TEST_PG_URL"), reason="set LNX_TEST_PG_URL to a PostgreSQL with pgvector")
def test_pgvector_store_roundtrip():
    from sqlalchemy import create_engine

    from app.engine.vectors import PgVectorStore

    engine = create_engine(os.environ["LNX_TEST_PG_URL"])
    assert PgVectorStore.available(engine)
    store = PgVectorStore(engine)
    store.save(9001, "m:1", ["a", "b", "c"], [[1, 0, 0], [0, 1, 0], [0.7, 0.7, 0]])
    hits = store.search(9001, "m:1", [1, 0.1, 0], 2)
    assert [h[0] for h in hits] == ["a", "c"] and hits[0][1] > 0.99
    assert store.search(9001, "other-model", [1, 0, 0], 2) == []
    store.delete(9001)
    assert store.search(9001, "m:1", [1, 0, 0], 2) == []


# ------------------------------------------------------------------ provider adapters

def _mock(monkeypatch, handler):
    real = httpx.Client
    monkeypatch.setattr(embeddings.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_ollama_adapter_batches_and_prefixes(monkeypatch):
    seen = []

    def handler(req):
        body = json.loads(req.content)
        assert req.url == "http://localhost:11434/api/embed" and body["model"] == "nomic-embed-text"
        seen.append(body["input"])
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]] * len(body["input"])})

    _mock(monkeypatch, handler)
    cfg = EmbeddingConfig("ollama", "nomic-embed-text", batch_size=2)
    assert len(embed_texts(cfg, ["a", "b", "c"], kind="document")) == 3
    assert len(seen) == 2 and seen[0][0] == "search_document: a"
    embed_texts(cfg, ["q"], kind="query")
    assert seen[-1] == ["search_query: q"]


def test_openai_adapter_orders_by_index(monkeypatch):
    def handler(req):
        assert req.url == "https://api.openai.com/v1/embeddings" and req.headers["authorization"] == "Bearer k"
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]})

    _mock(monkeypatch, handler)
    assert embed_texts(EmbeddingConfig("openai", "text-embedding-3-small", api_key="k"), ["x", "y"]) == [[1.0], [2.0]]


def test_azure_and_gemini_adapters(monkeypatch):
    def handler(req):
        if "openai.azure.com" in str(req.url):
            assert "/openai/deployments/emb/embeddings" in str(req.url) and req.headers["api-key"] == "k"
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})
        body = json.loads(req.content)
        assert ":batchEmbedContents" in str(req.url) and body["requests"][0]["taskType"] == "RETRIEVAL_QUERY"
        return httpx.Response(200, json={"embeddings": [{"values": [0.5, 0.5]}]})

    _mock(monkeypatch, handler)
    az = EmbeddingConfig("azure_openai", "", api_key="k", base_url="https://r.openai.azure.com", deployment="emb")
    assert az.configured and embed_texts(az, ["x"]) == [[1.0, 0.0]]
    assert embed_texts(EmbeddingConfig("gemini", "text-embedding-004", api_key="k"), ["q"], kind="query") == [[0.5, 0.5]]


def test_embedding_http_error(monkeypatch):
    _mock(monkeypatch, lambda req: httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(embeddings.EmbeddingError, match="HTTP 401"):
        embed_texts(EmbeddingConfig("openai", "m", api_key="bad"), ["x"])


# ------------------------------------------------------------------ end to end through the API

def test_api_hybrid_flow(client, auth, sample_project, monkeypatch):
    def handler(req):
        body = json.loads(req.content)
        texts = [re.sub(r"^search_(query|document): ", "", t) for t in body["input"]]
        return httpx.Response(200, json={"embeddings": [concept_vector(t) for t in texts]})

    _mock(monkeypatch, handler)
    pid = sample_project
    r = client.put("/api/settings/embeddings", json={"enabled": True, "provider": "ollama", "model": "nomic-embed-text"},
                   headers=auth).json()
    assert r["active"] and r["vector_store"] == "file"
    assert client.post("/api/settings/embeddings/test", headers=auth).json()["ok"]

    state = client.get(f"/api/projects/{pid}/semantic-index", headers=auth).json()
    assert state["search_mode"] == "keyword" and state["status"] == "none"
    assert client.post(f"/api/projects/{pid}/semantic-index", headers=auth).status_code == 202
    state = client.get(f"/api/projects/{pid}/semantic-index", headers=auth).json()
    assert state["status"] == "ready" and state["usable"] and state["embedded_count"] == state["chunk_count"] > 0

    ans = client.post(f"/api/projects/{pid}/ask", json={"question": QUESTION}, headers=auth).json()
    assert ans["retrieval"]["mode"] == "hybrid" and not ans["not_found"]
    assert ans["evidence"][0]["found_by"] in ("semantic", "both")

    # Changing the model makes the index stale: search falls back to keyword until rebuilt.
    client.put("/api/settings/embeddings", json={"model": "mxbai-embed-large"}, headers=auth)
    state = client.get(f"/api/projects/{pid}/semantic-index", headers=auth).json()
    assert not state["usable"] and "model changed" in state["stale_reason"]
    ans = client.post(f"/api/projects/{pid}/ask", json={"question": QUESTION}, headers=auth).json()
    assert ans["retrieval"]["mode"] == "keyword"
    client.put("/api/settings/embeddings", json={"enabled": False}, headers=auth)
