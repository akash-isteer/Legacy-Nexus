"""Vector stores for semantic search.

* PgVectorStore: PostgreSQL + pgvector. Used automatically when the database is PostgreSQL and the
  extension is available. Vectors live next to the rest of the data and share its backups.
* FileVectorStore: a normalised float32 matrix per collection on disk, searched with NumPy. Zero setup;
  the default for SQLite / development and the fallback when pgvector is not installed.

Both store cosine-normalised vectors keyed by (collection, chunk id) and tagged with the embedding model,
so vectors from different models are never compared.

There are two isolated namespaces, each with its own store:
  "code" — Code Q&A: one collection per project, in the main database (table lnx_chunk_vectors) or
           STORAGE_DIR/projects/<id>/vectors
  "docs" — Document Q&A: one collection per document, in the Document Q&A database (table lnx_doc_vectors)
           or STORAGE_DIR/docqa/<id>/vectors
"""
from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger("legacynexus.vectors")


def _normalise(vectors) -> np.ndarray:
    m = np.asarray(vectors, dtype=np.float32)
    if m.ndim == 1:
        m = m[None, :]
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


class FileVectorStore:
    name = "file"
    _cache: OrderedDict[tuple[str, float], tuple[dict, np.ndarray]] = OrderedDict()
    _lock = threading.Lock()

    def __init__(self, root: Path, folder: str = "projects/{id}/vectors"):
        self.root = root
        self.folder = folder

    def _dir(self, project_id: int) -> Path:
        return self.root / self.folder.format(id=project_id)

    def save(self, project_id: int, model_key: str, chunk_ids: list[str], vectors) -> None:
        d = self._dir(project_id)
        d.mkdir(parents=True, exist_ok=True)
        matrix = _normalise(vectors)
        tmp = d / "matrix.tmp.npy"
        np.save(tmp, matrix)
        tmp.replace(d / "matrix.npy")
        (d / "meta.json").write_text(json.dumps({"model_key": model_key, "chunk_ids": chunk_ids,
                                                 "dim": int(matrix.shape[1])}))

    def _load(self, project_id: int) -> tuple[dict, np.ndarray] | None:
        d = self._dir(project_id)
        meta_file, matrix_file = d / "meta.json", d / "matrix.npy"
        if not meta_file.exists() or not matrix_file.exists():
            return None
        where = str(d)  # unique per namespace and collection, so code and document vectors never share a slot
        key = (where, matrix_file.stat().st_mtime)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        loaded = (json.loads(meta_file.read_text()), np.load(matrix_file, mmap_mode="r"))
        with self._lock:
            for k in [k for k in self._cache if k[0] == where]:
                self._cache.pop(k)
            self._cache[key] = loaded
            while len(self._cache) > 8:
                self._cache.popitem(last=False)
        return loaded

    def search(self, project_id: int, model_key: str, query_vector, top_k: int) -> list[tuple[str, float]]:
        loaded = self._load(project_id)
        if loaded is None:
            return []
        meta, matrix = loaded
        if meta["model_key"] != model_key:
            return []
        q = _normalise(query_vector)[0]
        if q.shape[0] != matrix.shape[1]:
            return []
        sims = matrix @ q
        k = min(top_k, len(sims))
        idx = np.argpartition(-sims, k - 1)[:k]
        idx = idx[np.argsort(-sims[idx])]
        return [(meta["chunk_ids"][i], float(sims[i])) for i in idx]

    def delete(self, project_id: int) -> None:
        where = str(self._dir(project_id))
        with self._lock:  # release the memory-mapped copy first: Windows cannot delete a mapped file
            for k in [k for k in self._cache if k[0] == where]:
                self._cache.pop(k)
        for name in ("matrix.npy", "meta.json"):
            (self._dir(project_id) / name).unlink(missing_ok=True)

    def count(self, project_id: int) -> int:
        loaded = self._load(project_id)
        return len(loaded[0]["chunk_ids"]) if loaded else 0


class PgVectorStore:
    name = "pgvector"
    TABLE = "lnx_chunk_vectors"  # Code Q&A table (main database)

    def __init__(self, engine: Engine, table: str = TABLE, key_column: str = "project_id"):
        self.engine = engine
        self.table = table
        self.key = key_column

    @classmethod
    def available(cls, engine: Engine, table: str = TABLE, key_column: str = "project_id") -> bool:
        if engine.dialect.name != "postgresql":
            return False
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                conn.execute(text(
                    f"CREATE TABLE IF NOT EXISTS {table} ("
                    f" {key_column} INTEGER NOT NULL,"
                    " chunk_id TEXT NOT NULL,"
                    " model_key TEXT NOT NULL,"
                    " embedding vector NOT NULL,"
                    f" PRIMARY KEY ({key_column}, chunk_id))"))
            return True
        except Exception as exc:  # extension missing or no privilege: fall back to the file store
            log.warning("pgvector is not available (%s); using the file vector store", exc)
            return False

    @staticmethod
    def _literal(vector) -> str:
        return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"

    def save(self, project_id: int, model_key: str, chunk_ids: list[str], vectors) -> None:
        matrix = _normalise(vectors)
        rows = [{"p": project_id, "c": cid, "m": model_key, "e": self._literal(v)} for cid, v in zip(chunk_ids, matrix)]
        with self.engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {self.table} WHERE {self.key} = :p"), {"p": project_id})
            for start in range(0, len(rows), 500):
                conn.execute(text(f"INSERT INTO {self.table} ({self.key}, chunk_id, model_key, embedding) "
                                  "VALUES (:p, :c, :m, CAST(:e AS vector))"), rows[start:start + 500])

    def search(self, project_id: int, model_key: str, query_vector, top_k: int) -> list[tuple[str, float]]:
        q = self._literal(_normalise(query_vector)[0])
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                f"SELECT chunk_id, 1 - (embedding <=> CAST(:q AS vector)) AS sim FROM {self.table} "
                f"WHERE {self.key} = :p AND model_key = :m AND vector_dims(embedding) = vector_dims(CAST(:q AS vector)) "
                "ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"),
                {"q": q, "p": project_id, "m": model_key, "k": top_k}).all()
        return [(r[0], float(r[1])) for r in rows]

    def delete(self, project_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {self.table} WHERE {self.key} = :p"), {"p": project_id})

    def count(self, project_id: int) -> int:
        with self.engine.connect() as conn:
            return int(conn.execute(text(f"SELECT COUNT(*) FROM {self.table} WHERE {self.key} = :p"),
                                    {"p": project_id}).scalar() or 0)


# Namespace -> where its vectors live. Each namespace is chosen and created independently.
DOC_TABLE = "lnx_doc_vectors"
NAMESPACES = {
    "code": {"setting": "vector_store", "engine": "engine", "table": PgVectorStore.TABLE, "key": "project_id",
             "folder": "projects/{id}/vectors"},
    "docs": {"setting": "docqa_vector_store", "engine": "docqa_engine", "table": DOC_TABLE, "key": "document_id",
             "folder": "docqa/{id}/vectors"},
}

_stores: dict[str, object] = {}
_store_lock = threading.Lock()


def get_store(namespace: str = "code"):
    """Pick a namespace's vector store once per process, following its setting (auto | pgvector | file)."""
    with _store_lock:
        if namespace not in _stores:
            from app import database
            from app.config import get_settings

            spec = NAMESPACES[namespace]
            settings = get_settings()
            choice = getattr(settings, spec["setting"])
            engine = getattr(database, spec["engine"])
            if choice in ("auto", "pgvector") and PgVectorStore.available(engine, spec["table"], spec["key"]):
                store = PgVectorStore(engine, spec["table"], spec["key"])
            else:
                if choice == "pgvector":
                    log.warning("%s=pgvector requested but unavailable; using the file store", spec["setting"].upper())
                store = FileVectorStore(settings.storage_dir, spec["folder"])
            _stores[namespace] = store
            log.info("Vector store (%s): %s", namespace, store.name)
        return _stores[namespace]


def reset_store() -> None:
    with _store_lock:
        _stores.clear()
