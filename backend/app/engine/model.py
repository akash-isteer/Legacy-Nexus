"""Parse every source file of a project into one consolidated, resolved application model."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.engine.parse_cache import FileParseCache
from app.engine.resolve import resolve_model
from app.parsers import registry
from app.parsers.detect import detect_language, is_ignored_path

def read_text(path: Path) -> str:
    """Whole file as text (no size cut-off), trying UTF-8, then Windows-1252, then Latin-1."""
    raw = path.read_bytes()
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def inventory(root: Path) -> list[dict]:
    """List analysable files under root with language, size and line counts."""
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel_parts = path.relative_to(root).parts
        if any(part.startswith(".") for part in rel_parts) or is_ignored_path(rel_parts):
            continue
        text = read_text(path)
        lang = detect_language(path, text)
        if not lang:
            continue
        files.append({
            "path": path.relative_to(root).as_posix(),
            "language": lang,
            "size_bytes": path.stat().st_size,
            "line_count": text.count("\n") + (0 if text.endswith("\n") or not text else 1),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    return files


def build_model(root: Path, files: list[dict], cache: FileParseCache | None = None) -> dict:
    """Parse every file through its language adapter and merge the results into one model.

    With a cache, files whose content hash was seen before are not parsed again.
    """
    model: dict = {
        "programs": {}, "copybooks": {}, "tables": {}, "routines": {}, "jobs": {}, "apis": {},
        "documents": [], "views": [], "sql_statements": [], "files": {f["path"]: f for f in files},
        "warnings": [],
    }
    for f in files:
        path, lang = f["path"], f["language"]
        adapter = registry.get(lang)
        if adapter is None:
            model["warnings"].append(f"No parser registered for language '{lang}' ({path})")
            continue
        try:
            key = FileParseCache.key(adapter.cache_tag, f["sha256"]) if cache and f.get("sha256") else None
            fragment = cache.get(key) if key else None
            if fragment is None:
                # JSON round trip: fresh and cached fragments must be identical (tuples become lists).
                fragment = json.loads(json.dumps(adapter.parse(path, read_text(root / path))))
                if key:
                    cache.put(key, fragment)
            adapter.merge(model, path, fragment)
        except Exception as exc:  # one bad file must never stop the whole analysis
            model["warnings"].append(f"Could not parse {path}: {exc}")
    model.setdefault("configs", [])
    resolve_model(model)
    _resolve_sql_columns(model)
    return model


def _resolve_sql_columns(model: dict) -> None:
    """Attach unqualified column references to the right table using the DDL, and drop non-columns."""
    schema = {name: {c["name"] for c in t["columns"]} for name, t in model["tables"].items()}

    def resolve(cols: list, tables: list[str]) -> list:
        out = []
        for table, col in cols:
            if col == "*":
                out.append([table, col])
                continue
            if table is None:
                owners = [t for t in tables if col in schema.get(t, set())]
                table = owners[0] if len(owners) == 1 else (tables[0] if len(tables) == 1 else None)
            if table in schema and col not in schema[table]:
                continue  # parameter or host expression, not a real column
            out.append([table, col])
        return out

    def fix(st: dict) -> None:
        tables = list(dict.fromkeys(st.get("tables_read", []) + st.get("tables_written", [])))
        for key in ("columns_read", "columns_written", "filter_columns", "select_columns"):
            st[key] = resolve(st.get(key, []), tables)
        st["assignments"] = [
            [t or (tables[0] if len(tables) == 1 else None), c, v] for t, c, v in st.get("assignments", [])
        ]

    for prog in model["programs"].values():
        for block in prog["sql_blocks"]:
            fix(block["analysis"])
    for routine in model["routines"].values():
        for st in routine["statements"]:
            fix(st)
    for st in model["sql_statements"]:
        fix(st)
