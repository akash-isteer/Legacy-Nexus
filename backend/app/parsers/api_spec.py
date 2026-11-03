"""OpenAPI / Swagger parser for documented interfaces."""
from __future__ import annotations

import json
import re

import yaml

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def parse_api_spec(path: str, text: str) -> dict | None:
    try:
        doc = json.loads(text) if path.lower().endswith(".json") else yaml.safe_load(text)
    except (yaml.YAMLError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict) or not ({"openapi", "swagger"} & set(doc)):
        return None
    info = doc.get("info") or {}
    lines = text.split("\n")

    def line_of(needle: str) -> int:
        for i, ln in enumerate(lines, start=1):
            if needle in ln:
                return i
        return 1

    api_id = str(info.get("x-api-id") or info.get("title") or path.split("/")[-1]).strip()
    operations = []
    for route, item in (doc.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        shared = _parameters(item.get("parameters"))
        for method, op in item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(op, dict):
                continue
            own = _parameters(op.get("parameters"))
            params = own + [p for p in shared if (p["name"], p["in"]) not in {(o["name"], o["in"]) for o in own}]
            if isinstance(op.get("requestBody"), dict):
                params.append({"name": "body", "in": "body", "required": bool(op["requestBody"].get("required"))})
            operations.append({
                "parameters": params,
                "method": method.upper(),
                "path": route,
                "operation_id": op.get("operationId"),
                "summary": op.get("summary") or op.get("description") or "",
                "legacy_program": op.get("x-legacy-program"),
                "line": line_of(f"{route}:") if method == next(iter(item)) else line_of(f"operationId: {op.get('operationId')}"),
            })
    programs = [str(p).upper() for p in (doc.get("x-legacy-programs") or info.get("x-legacy-programs") or [])]
    programs += [str(o["legacy_program"]).upper() for o in operations if o.get("legacy_program")]
    return {
        "id": re.sub(r"\s+", "_", api_id),
        "title": info.get("title") or api_id,
        "version": info.get("version"),
        "description": info.get("description") or "",
        "operations": operations,
        "backing_programs": list(dict.fromkeys(programs)),
        "line": line_of("info:"),
        "documented": True,
        "source": "spec",
    }


def _parameters(raw) -> list[dict]:
    out = []
    for p in raw if isinstance(raw, list) else []:
        if isinstance(p, dict) and p.get("name") and p.get("in"):
            out.append({"name": str(p["name"]), "in": str(p["in"]),
                        "required": bool(p.get("required")) or p.get("in") == "path"})
    return out
