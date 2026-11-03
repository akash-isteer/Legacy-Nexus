"""Adapters for the built-in languages: COBOL, copybooks, JCL, SQL / PL-SQL, OpenAPI and plain documents."""
from __future__ import annotations

from pathlib import Path

from app.parsers.api_spec import parse_api_spec
from app.parsers.base import LanguageAdapter
from app.parsers.cobol import parse_cobol
from app.parsers.jcl import parse_jcl
from app.parsers.sql import parse_sql_file


class CobolAdapter(LanguageAdapter):
    language = "cobol"

    def parse(self, path: str, text: str) -> dict:
        return parse_cobol(path, text).to_dict()

    def merge(self, model: dict, path: str, prog: dict) -> None:
        key = prog["program_id"]
        if key in model["programs"]:
            model["warnings"].append(f"Duplicate PROGRAM-ID {key} in {path}; kept as {key}@{path}")
            key = f"{key}@{path}"
            prog["program_id"] = key
        model["programs"][key] = prog


class CopybookAdapter(LanguageAdapter):
    language = "copybook"

    def parse(self, path: str, text: str) -> dict:
        return parse_cobol(path, text, is_copybook=True).to_dict()

    def merge(self, model: dict, path: str, cb: dict) -> None:
        name = Path(path).stem.upper()
        cb["program_id"] = name
        model["copybooks"][name] = cb


class JclAdapter(LanguageAdapter):
    language = "jcl"

    def parse(self, path: str, text: str) -> dict:
        return parse_jcl(path, text)

    def merge(self, model: dict, path: str, job: dict) -> None:
        job["path"] = path
        model["jobs"][job["name"]] = job


class SqlAdapter(LanguageAdapter):
    language = "sql"

    def parse(self, path: str, text: str) -> dict:
        res = parse_sql_file(text)
        return {"tables": res.tables, "routines": res.routines, "views": res.views, "statements": res.statements}

    def merge(self, model: dict, path: str, res: dict) -> None:
        for t in res["tables"]:
            t["path"] = path
            model["tables"][t["name"]] = t
        for r in res["routines"]:
            r["path"] = path
            model["routines"][r["name"]] = r
        for v in res["views"]:
            v["path"] = path
            model["views"].append(v)
        for s in res["statements"]:
            s["path"] = path
            model["sql_statements"].append(s)


class ApiSpecAdapter(LanguageAdapter):
    language = "api"
    version = "2"  # v2: operation parameters, documented/source flags

    def parse(self, path: str, text: str) -> dict | None:
        return parse_api_spec(path, text)

    def merge(self, model: dict, path: str, api: dict | None) -> None:
        if api:
            api["path"] = path
            model["apis"][api["id"]] = api


class DocumentAdapter(LanguageAdapter):
    language = "doc"

    def parse(self, path: str, text: str) -> dict:
        title = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.strip()), Path(path).name)
        return {"title": title}

    def merge(self, model: dict, path: str, doc: dict) -> None:
        model["documents"].append({"path": path, "title": doc["title"]})


class CodeAdapter(LanguageAdapter):
    """Modern languages parsed with tree-sitter. Each file yields one unit per class (or module)."""

    version = "1"

    def __init__(self, language: str, parse_fn):
        self.language = language
        self._parse = parse_fn

    def parse(self, path: str, text: str) -> dict:
        return self._parse(path, text)

    def merge(self, model: dict, path: str, fragment: dict) -> None:
        for unit in fragment["units"]:
            key = unit["program_id"]
            if key in model["programs"]:
                model["warnings"].append(f"Duplicate name {key} in {path}; kept as {key}@{path}")
                key = f"{key}@{path}"
                unit["program_id"] = key
            model["programs"][key] = unit


class ShellAdapter(LanguageAdapter):
    language = "shell"

    def parse(self, path: str, text: str) -> dict:
        from app.parsers.code.shell import parse_shell

        return parse_shell(path, text)

    def merge(self, model: dict, path: str, job: dict) -> None:
        job["path"] = path
        name = job["name"]
        if name in model["jobs"]:
            name = f"{name}@{path}"
            job["name"] = name
        model["jobs"][name] = job


class ConfigAdapter(LanguageAdapter):
    language = "config"

    def parse(self, path: str, text: str) -> dict:
        from app.parsers.code.config import parse_config

        return parse_config(path, text)

    def merge(self, model: dict, path: str, cfg: dict) -> None:
        model.setdefault("configs", []).append({**cfg, "path": path})


def _code_adapters() -> list[LanguageAdapter]:
    from app.parsers.code.csharp import parse_csharp
    from app.parsers.code.java import parse_java
    from app.parsers.code.javascript import parse_javascript
    from app.parsers.code.python_lang import parse_python

    return [CodeAdapter("java", parse_java), CodeAdapter("csharp", parse_csharp),
            CodeAdapter("python", parse_python), CodeAdapter("javascript", parse_javascript),
            CodeAdapter("typescript", parse_javascript)]


BUILTIN_ADAPTERS: list[LanguageAdapter] = [
    CobolAdapter(), CopybookAdapter(), JclAdapter(), SqlAdapter(), ApiSpecAdapter(), DocumentAdapter(),
    ShellAdapter(), ConfigAdapter(), *_code_adapters(),
]
