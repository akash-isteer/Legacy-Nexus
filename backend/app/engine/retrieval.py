"""Code chunking and lexical retrieval (BM25 + entity and graph boosts).

Only small, line-numbered chunks are ever sent to an LLM; whole files never are.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from app.engine.model import read_text

MAX_CHUNK_LINES = 60
STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "of", "to", "in", "on", "for", "and", "or", "it", "this",
    "that", "what", "where", "which", "who", "how", "does", "do", "did", "with", "by", "from", "at", "as", "i",
    "we", "you", "can", "could", "should", "would", "will", "about", "into", "if", "when", "then", "there", "any",
    "me", "my", "our", "us", "tell", "show", "explain", "please", "happens", "happen", "used", "use", "uses",
    "code", "program", "programs", "file", "files", "value", "values", "data",
}
SYNONYMS = {
    "calcul": ["comput"], "comput": ["calcul"], "posted": ["post"], "fee": ["charg"], "charg": ["fee"],
    "customer": ["cust"], "account": ["acct"], "payment": ["pay"], "address": ["addr"], "balanc": ["bal"],
    "approv": ["approval", "limit"], "rule": ["if"], "validat": ["valid", "if"],
}


def stem(word: str) -> str:
    for suffix in ("ations", "ation"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)] + "at"
    for suffix in ("ings", "ing", "ions", "ion", "ed", "es", "s", "e"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    out = []
    for raw in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", text):
        low = raw.lower()
        parts = [p for p in re.split(r"[-_]", low) if p]
        if len(parts) > 1:
            out.append(low.replace("_", "-"))
        for p in parts:
            if p not in STOPWORDS and not p.isdigit():
                out.append(stem(p))
    return out


def build_chunks(root: Path, model: dict) -> list[dict]:
    chunks: list[dict] = []

    def add(path: str, start: int, end: int, kind: str, title: str, program: str | None, lines: list[str],
            symbols: list[str]) -> None:
        for s in range(start, end + 1, MAX_CHUNK_LINES):
            e = min(end, s + MAX_CHUNK_LINES - 1)
            body = lines[s - 1:e]
            if not any(x.strip() for x in body):
                continue
            chunks.append({
                "id": f"C{len(chunks) + 1}", "file": path, "start_line": s, "end_line": e, "kind": kind,
                "title": title, "program": program, "symbols": symbols, "text": "\n".join(body),
            })

    for name, p in model["programs"].items():
        lines = read_text(root / p["path"]).split("\n")
        start = p.get("start_line", 1)  # a file can hold several classes; each starts at its own line
        first_proc = min((x["start_line"] for x in p["paragraphs"]), default=p.get("end_line", len(lines)) + 1)
        header_end = first_proc - 1
        items = [d["name"] for d in p["data_items"]]
        title = f"{name} identification and data" if p.get("language", "cobol") == "cobol" else f"{name} declaration"
        add(p["path"], start, min(header_end, len(lines)), "program-header", title, name, lines, [name] + items[:80])
        for para in p["paragraphs"]:
            stmts = [s for s in p["statements"] if s["paragraph"] == para["name"]]
            syms = {name, para["name"]}
            for s in stmts:
                syms.update(re.findall(r"[A-Z][A-Z0-9-]{2,}", s["text"]))
            for b in p["sql_blocks"]:
                if para["start_line"] <= b["start_line"] <= para["end_line"]:
                    a = b["analysis"]
                    syms.update(a.get("tables_read", []) + a.get("tables_written", []))
                    syms.update(c for _, c in a.get("columns_read", []) + a.get("select_columns", []) + a.get("columns_written", []))
            add(p["path"], para["start_line"], para["end_line"], "paragraph", f"{name} · {para['name']}", name,
                lines, sorted(syms))
    for name, cb in model["copybooks"].items():
        lines = read_text(root / cb["path"]).split("\n")
        add(cb["path"], 1, len(lines), "copybook", f"Copybook {name}", None, lines,
            [name] + [d["name"] for d in cb["data_items"]])
    for name, job in model["jobs"].items():
        lines = read_text(root / job["path"]).split("\n")
        syms = [name] + [s["program"] for s in job["steps"] if s["program"]] + [
            d["dsn"] for s in job["steps"] for d in s["dds"] if d["dsn"]] + [c["target"] for s in job["steps"] for c in s["calls"]]
        add(job["path"], 1, len(lines), "jcl", f"Job {name}", None, lines, syms)
    sql_files: dict[str, list[tuple[int, int, str, list[str]]]] = defaultdict(list)
    for name, t in model["tables"].items():
        end = max([c["line"] for c in t["columns"]] + [t["line"]]) + 1
        sql_files[t["path"]].append((t["line"], end, f"Table {name}", [name] + [c["name"] for c in t["columns"]]))
    for name, r in model["routines"].items():
        syms = [name] + [x for st in r["statements"] for x in st.get("tables_read", []) + st.get("tables_written", [])]
        sql_files[r["path"]].append((r["line"], r["end_line"], f"Procedure {name}", syms))
    for path, parts in sql_files.items():
        lines = read_text(root / path).split("\n")
        for start, end, title, syms in parts:
            add(path, start, min(end, len(lines)), "sql", title, None, lines, syms)
    for api_id, api in model["apis"].items():
        lines = read_text(root / api["path"]).split("\n")
        add(api["path"], 1, len(lines), "api", f"API {api_id}", None, lines, [api_id] + api["backing_programs"])
    for doc in model["documents"]:
        lines = read_text(root / doc["path"]).split("\n")
        add(doc["path"], 1, len(lines), "document", doc["title"], None, lines, [])
    return chunks


class BM25Index:
    def __init__(self, chunks: list[dict], k1: float = 1.4, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(f"{c['title']} {c['text']} {' '.join(c['symbols'])}")) for c in chunks]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lengths) / len(self.lengths)) if self.lengths else 1.0
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def expand(self, terms: list[str]) -> list[str]:
        out = list(terms)
        for t in terms:
            for key, alts in SYNONYMS.items():
                if t.startswith(key):
                    out += [stem(a) for a in alts]
        return out

    def search(self, query: str, boosts: dict[str, float] | None = None, top_k: int = 8) -> list[tuple[dict, float]]:
        terms = self.expand(tokenize(query))
        scores = []
        for i, doc in enumerate(self.docs):
            s = 0.0
            for t in terms:
                f = doc.get(t)
                if not f:
                    continue
                idf = self.idf.get(t, 0.0)
                s += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avg))
            if boosts:
                s += boosts.get(self.chunks[i]["id"], 0.0)
            if s > 0:
                scores.append((self.chunks[i], s))
        scores.sort(key=lambda x: -x[1])
        return scores[:top_k]


def build_symbols(model: dict, graph: dict, lineage: dict) -> list[dict]:
    """Searchable index of every named thing, used by search boxes, Q&A entity linking and validation."""
    out: list[dict] = []
    for n in graph["nodes"]:
        out.append({"name": n["label"], "type": n["type"], "id": n["id"], "file": n.get("file"), "line": n.get("line")})
    for name, p in model["programs"].items():
        for para in p["paragraphs"]:
            out.append({"name": para["name"], "type": "paragraph", "id": f"paragraph:{name}.{para['name']}",
                        "program": name, "file": p["path"], "line": para["start_line"]})
        for d in p["data_items"]:
            out.append({"name": d["name"], "type": "field", "id": f"field:{name}.{d['name']}", "program": name,
                        "file": p["path"], "line": d["line"]})
        for cname, c in p["conditions"].items():
            out.append({"name": cname, "type": "condition", "id": f"field:{name}.{c['parent']}", "program": name,
                        "file": p["path"], "line": c["line"]})
    for name, cb in model["copybooks"].items():
        for d in cb["data_items"]:
            out.append({"name": d["name"], "type": "field", "id": f"copybook:{name}", "program": None,
                        "file": cb["path"], "line": d["line"]})
    for path in model["files"]:
        out.append({"name": Path(path).name.upper(), "type": "file", "id": f"file:{path}", "file": path, "line": 1})
    seen, unique = set(), []
    for s in out:
        key = (s["id"], s["name"])
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique
