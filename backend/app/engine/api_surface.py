"""API surface, documentation drift and breaking-change analysis.

* Surface: every operation, from OpenAPI specs and from endpoints found in code, with its parameters.
* Drift: operations implemented but not documented, and documented but not implemented.
* Changes: comparing two analyses of the same application, which changes break existing consumers
  (removed operations, new required inputs, parameters moved) and which are safe (additions).
"""
from __future__ import annotations

from app.engine.resolve import normalize_path

SOURCE_LABEL = {"spec": "documented", "code": "implemented"}


def build_surface(model: dict) -> list[dict]:
    ops = []
    for api_id, api in sorted(model["apis"].items()):
        source = api.get("source", "spec")
        for o in api["operations"]:
            norm = normalize_path(o["path"])
            ops.append({
                "key": f"{o['method']} {norm}", "method": o["method"], "path": o["path"], "normalized": norm,
                "source": source, "api": api_id,
                "program": o.get("legacy_program") if source == "code" else o.get("implemented_by"),
                "file": api.get("path"), "line": o.get("line"),
                "params": sorted(({"name": p["name"], "in": p["in"], "required": bool(p.get("required"))}
                                  for p in o.get("parameters") or [] if p.get("in") != "path"),
                                 key=lambda p: (p["in"], p["name"])),
            })
    return ops


def drift(surface: list[dict]) -> dict:
    documented = {o["key"]: o for o in surface if o["source"] == "spec"}
    implemented = {o["key"]: o for o in surface if o["source"] == "code"}
    # A spec is only checked against code when at least one of its operations is implemented in the
    # uploaded code; specs fronting mainframe programs (no REST code) are not reported as missing.
    specs_with_code = {o["api"] for k, o in documented.items() if k in implemented}
    return {
        "undocumented": [_brief(o) for k, o in sorted(implemented.items()) if k not in documented],
        "not_implemented": [_brief(o) for k, o in sorted(documented.items())
                            if k not in implemented and o["api"] in specs_with_code],
        "matched": sorted(k for k in documented if k in implemented),
    }


def diff_surfaces(old: list[dict], new: list[dict]) -> dict:
    """Breaking and safe changes between two surfaces, compared per source (documented vs implemented)."""
    breaking, safe = [], []
    before = {(o["source"], o["key"]): o for o in old}
    after = {(o["source"], o["key"]): o for o in new}
    for sk, o in sorted(before.items()):
        if sk not in after:
            breaking.append(_change("removed", o, f"{o['method']} {o['path']} was removed; existing callers will fail"))
    for sk, o in sorted(after.items()):
        if sk not in before:
            safe.append(_change("added", o, f"{o['method']} {o['path']} was added"))
            continue
        prev = before[sk]
        old_params = {p["name"]: p for p in prev["params"]}
        new_params = {p["name"]: p for p in o["params"]}
        for name, p in new_params.items():
            was = old_params.get(name)
            if was is None:
                if p["required"]:
                    breaking.append(_change("required_parameter_added", o,
                                            f"New required {p['in']} parameter '{name}'; callers that omit it will fail"))
                else:
                    safe.append(_change("optional_parameter_added", o, f"New optional {p['in']} parameter '{name}'"))
            elif p["required"] and not was["required"]:
                breaking.append(_change("parameter_now_required", o, f"Parameter '{name}' is now required"))
            elif p["in"] != was["in"]:
                breaking.append(_change("parameter_moved", o, f"Parameter '{name}' moved from {was['in']} to {p['in']}"))
        for name, was in old_params.items():
            if name not in new_params:
                safe.append(_change("parameter_removed", o, f"Parameter '{name}' is no longer read"))
        if prev.get("program") and o.get("program") and prev["program"] != o["program"]:
            safe.append(_change("implementation_moved", o, f"Now implemented by {o['program']} (was {prev['program']})"))
    return {"breaking": breaking, "non_breaking": safe,
            "summary": {"breaking": len(breaking), "non_breaking": len(safe)}}


def _brief(o: dict) -> dict:
    return {"method": o["method"], "path": o["path"], "api": o["api"], "program": o.get("program"),
            "file": o.get("file"), "line": o.get("line")}


def _change(kind: str, o: dict, message: str) -> dict:
    return {"kind": kind, "method": o["method"], "path": o["path"], "source": SOURCE_LABEL.get(o["source"], o["source"]),
            "api": o["api"], "message": message, "file": o.get("file"), "line": o.get("line")}
