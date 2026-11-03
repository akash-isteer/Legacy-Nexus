"""Embedding provider adapters over plain HTTPS, used for semantic (vector) search.

Embeddings are optional. Without them, search is keyword-only (BM25 + graph boosts).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

# Model names are suggestions; any model id the provider serves can be typed in.
EMBEDDING_CATALOG: dict[str, dict] = {
    "ollama": {
        "label": "Ollama (local, code never leaves your network)",
        "fields": ["model", "base_url"],
        "default_base_url": "http://localhost:11434",
        "model_suggestions": ["nomic-embed-text", "mxbai-embed-large", "bge-m3"],
        "key_hint": "",
    },
    "openai": {
        "label": "OpenAI",
        "fields": ["api_key", "model", "base_url"],
        "default_base_url": "https://api.openai.com/v1",
        "model_suggestions": ["text-embedding-3-small", "text-embedding-3-large"],
        "key_hint": "sk-…",
    },
    "azure_openai": {
        "label": "Azure OpenAI",
        "fields": ["api_key", "base_url", "deployment", "api_version"],
        "default_base_url": "",
        "default_api_version": "2024-10-21",
        "model_suggestions": [],
        "key_hint": "Azure resource key",
        "base_url_hint": "https://<resource>.openai.azure.com",
    },
    "gemini": {
        "label": "Google Gemini",
        "fields": ["api_key", "model"],
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "model_suggestions": ["text-embedding-004", "gemini-embedding-001"],
        "key_hint": "AIza…",
    },
    "openai_compatible": {
        "label": "OpenAI-compatible endpoint (vLLM, LM Studio, TEI, gateways)",
        "fields": ["api_key", "model", "base_url"],
        "default_base_url": "",
        "model_suggestions": [],
        "key_hint": "Optional",
        "base_url_hint": "https://your-gateway/v1",
    },
}

# Some open models are trained with task prefixes; using them improves retrieval quality.
_PREFIXES: list[tuple[str, str, str]] = [
    ("nomic-embed", "search_query: ", "search_document: "),
    ("e5", "query: ", "passage: "),
    ("bge-", "Represent this sentence for searching relevant passages: ", ""),
    ("mxbai-embed", "Represent this sentence for searching relevant passages: ", ""),
]


class EmbeddingError(RuntimeError):
    pass


@dataclass
class EmbeddingConfig:
    provider: str
    model: str
    api_key: str = ""
    base_url: str = ""
    api_version: str = ""
    deployment: str = ""
    batch_size: int = 32
    max_chars: int = 4000
    timeout_seconds: int = 120

    @property
    def configured(self) -> bool:
        if self.provider not in EMBEDDING_CATALOG:
            return False
        if self.provider == "azure_openai":
            return bool(self.api_key and self.base_url and self.deployment)
        if self.provider == "ollama":
            return bool(self.model)
        if self.provider == "openai_compatible":
            return bool(self.model and self.base_url)
        return bool(self.api_key and self.model)

    @property
    def key(self) -> str:
        """Identifies the vector space. Vectors from different models are never mixed."""
        name = self.deployment if self.provider == "azure_openai" else self.model
        return f"{self.provider}:{name}"

    def prefixes(self) -> tuple[str, str]:
        name = (self.deployment or self.model).lower()
        for marker, query, document in _PREFIXES:
            if marker in name:
                return query, document
        return "", ""


def embed_texts(cfg: EmbeddingConfig, texts: list[str], kind: str = "document",
                progress=None) -> list[list[float]]:
    """Embed texts in batches. kind is 'document' (indexing) or 'query' (a user question)."""
    if not texts:
        return []
    query_prefix, doc_prefix = cfg.prefixes()
    prefix = query_prefix if kind == "query" else doc_prefix
    prepared = [prefix + t[: cfg.max_chars] for t in texts]
    handler = {
        "ollama": _ollama, "openai": _openai, "openai_compatible": _openai, "azure_openai": _azure,
        "gemini": _gemini,
    }.get(cfg.provider)
    if handler is None:
        raise EmbeddingError(f"Unsupported embedding provider '{cfg.provider}'")
    out: list[list[float]] = []
    try:
        with httpx.Client(timeout=cfg.timeout_seconds) as client:
            for start in range(0, len(prepared), cfg.batch_size):
                batch = prepared[start:start + cfg.batch_size]
                vectors = _with_retry(lambda: handler(client, cfg, batch, kind))
                if len(vectors) != len(batch):
                    raise EmbeddingError(f"Provider returned {len(vectors)} vectors for {len(batch)} texts")
                out.extend(vectors)
                if progress:
                    progress(len(out), len(prepared))
    except httpx.TimeoutException as exc:
        raise EmbeddingError(f"The {cfg.provider} embedding request timed out") from exc
    except httpx.HTTPStatusError as exc:
        raise EmbeddingError(f"{cfg.provider} returned HTTP {exc.response.status_code}: {exc.response.text[:300]}") from exc
    except httpx.HTTPError as exc:
        raise EmbeddingError(f"Could not reach {cfg.provider}: {exc}") from exc
    dims = {len(v) for v in out}
    if len(dims) != 1 or 0 in dims:
        raise EmbeddingError("Provider returned vectors of inconsistent size")
    return out


def _with_retry(call, attempts: int = 3):
    """Retry rate limits and transient server errors with exponential backoff."""
    for attempt in range(attempts):
        try:
            return call()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
        except (httpx.ConnectError, httpx.ReadError):
            if attempt == attempts - 1:
                raise
        time.sleep(2 ** attempt)
    return None


def _ollama(client: httpx.Client, cfg: EmbeddingConfig, batch: list[str], kind: str) -> list[list[float]]:
    base = (cfg.base_url or EMBEDDING_CATALOG["ollama"]["default_base_url"]).rstrip("/")
    r = client.post(f"{base}/api/embed", json={"model": cfg.model, "input": batch, "truncate": True})
    r.raise_for_status()
    return r.json()["embeddings"]


def _openai_parse(r: httpx.Response) -> list[list[float]]:
    r.raise_for_status()
    data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
    return [d["embedding"] for d in data]


def _openai(client: httpx.Client, cfg: EmbeddingConfig, batch: list[str], kind: str) -> list[list[float]]:
    base = (cfg.base_url or EMBEDDING_CATALOG["openai"]["default_base_url"]).rstrip("/")
    headers = {"authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    return _openai_parse(client.post(f"{base}/embeddings", headers=headers, json={"model": cfg.model, "input": batch}))


def _azure(client: httpx.Client, cfg: EmbeddingConfig, batch: list[str], kind: str) -> list[list[float]]:
    version = cfg.api_version or EMBEDDING_CATALOG["azure_openai"]["default_api_version"]
    url = f"{cfg.base_url.rstrip('/')}/openai/deployments/{cfg.deployment}/embeddings"
    return _openai_parse(client.post(url, params={"api-version": version}, headers={"api-key": cfg.api_key},
                                     json={"input": batch}))


def _gemini(client: httpx.Client, cfg: EmbeddingConfig, batch: list[str], kind: str) -> list[list[float]]:
    base = (cfg.base_url or EMBEDDING_CATALOG["gemini"]["default_base_url"]).rstrip("/")
    task = "RETRIEVAL_QUERY" if kind == "query" else "RETRIEVAL_DOCUMENT"
    r = client.post(f"{base}/models/{cfg.model}:batchEmbedContents", headers={"x-goog-api-key": cfg.api_key}, json={
        "requests": [{"model": f"models/{cfg.model}", "content": {"parts": [{"text": t}]}, "taskType": task}
                     for t in batch],
    })
    r.raise_for_status()
    return [e["values"] for e in r.json()["embeddings"]]
