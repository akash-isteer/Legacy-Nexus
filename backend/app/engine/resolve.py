"""Cross-file resolution for modern languages, run after every file has been parsed.

Calls are resolved honestly, never guessed:
  * one certain target              -> a CALLS edge (``inferred`` when found by method name only)
  * several possible targets        -> an UNRESOLVED_CALL with the candidates (e.g. an interface with two
                                       implementations chosen at runtime, or an untyped Python/JS object)
  * no target in the uploaded code  -> a library call, not recorded
The same pass maps ORM access (repositories, DbSets, session.add) to tables, links shell-script steps to
the programs they start, publishes REST endpoints found in code as APIs, and links documented OpenAPI
operations to the code that implements them.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import PurePosixPath

COBOL_LANGUAGES = {None, "cobol"}
LANGUAGE_FAMILY = {"java": "jvm", "csharp": "dotnet", "python": "python", "javascript": "js", "typescript": "js"}
WRITE_PREFIXES = ("save", "delete", "insert", "update", "persist", "remove", "add", "create", "merge", "upsert")
EF_WRITES = {"Add", "AddRange", "Update", "UpdateRange", "Remove", "RemoveRange", "Attach", "AddAsync",
             "AddRangeAsync"}
SESSION_WRITES = {"add", "add_all", "merge", "delete", "save", "persist", "remove", "bulk_save_objects"}


def resolve_model(model: dict) -> None:
    programs = model["programs"]
    modern = {k: p for k, p in programs.items() if p.get("language") not in COBOL_LANGUAGES}
    if modern:
        idx = _Index(model, modern)
        _declare_orm_tables(model, modern, idx)
        for key, p in modern.items():
            _resolve_unit(key, p, idx)
        _publish_code_apis(model, modern)
    _link_spec_operations(model)
    _resolve_jobs(model)
    _flag_dynamic_cobol_calls(model)


class _Index:
    def __init__(self, model: dict, modern: dict):
        self.model = model
        self.programs = model["programs"]
        self.modern = modern
        self.by_name: dict[str, list[str]] = defaultdict(list)
        self.implementers: dict[str, list[str]] = defaultdict(list)
        self.methods: dict[str, list[str]] = defaultdict(list)
        for key, p in modern.items():
            self.by_name[key.split("@")[0]].append(key)
            for base in p["extends"]:
                self.implementers[base].append(key)
            for para in p["paragraphs"]:
                self.methods[para["name"]].append(key)
        self.entity_table: dict[str, str] = {}
        for key, p in modern.items():
            if p.get("entity_table"):
                self.entity_table[key.split("@")[0]] = p["entity_table"]

    def family(self, key: str) -> str | None:
        return LANGUAGE_FAMILY.get(self.programs[key].get("language"))

    def lookup(self, type_name: str | None, near: str) -> list[str]:
        """Units with this simple name, preferring the caller's language family."""
        found = [k for k in self.by_name.get(type_name or "", []) if k != near]
        same = [k for k in found if self.family(k) == self.family(near)]
        return same or found

    def table_of(self, entity: str | None) -> str | None:
        return self.entity_table.get(entity or "")


# ----------------------------------------------------------------------------- ORM tables


def _declare_orm_tables(model: dict, modern: dict, idx: _Index) -> None:
    """Entities, DbSets and repositories name tables; declare those the DDL does not already define."""
    def declare(name: str, p: dict, columns: list[dict]):
        if name in model["tables"]:
            return
        model["tables"][name] = {
            "name": name, "line": p["start_line"], "path": p["path"], "source": "orm", "foreign_keys": [],
            "columns": [{"name": c["name"], "type": "", "nullable": True, "primary_key": False, "line": c["line"]}
                        for c in columns],
        }

    for p in modern.values():
        if p.get("entity_table"):
            declare(p["entity_table"], p, p.get("entity_columns", []))
    for p in modern.values():
        for prop, entity in (p.get("dbsets") or {}).items():
            table = idx.table_of(entity) or prop.upper()
            idx.entity_table.setdefault(entity, table)
            declare(table, p, [])


def _table_access(p: dict, table: str, write: bool, call: dict, how: str) -> None:
    analysis = {"op": "UPDATE" if write else "SELECT", "tables_read": [] if write else [table],
                "tables_written": [table] if write else [], "columns_read": [], "columns_written": [],
                "filter_columns": [], "select_columns": [], "into_vars": [], "input_vars": [], "assignments": [],
                "cursor": None, "include": None, "called_procedure": None, "parsed_with": "orm"}
    p["sql_blocks"].append({"kind": "ORM", "start_line": call["line"], "end_line": call["line"], "text": how,
                            "ends_sentence": True, "analysis": analysis, "paragraph": call.get("paragraph")})


# ----------------------------------------------------------------------------- calls


def _resolve_unit(key: str, p: dict, idx: _Index) -> None:
    calls = []
    for c in p["raw_calls"]:
        outcome = _resolve_call(key, p, c, idx)
        if outcome:
            calls.append(outcome)
    seen = set()
    p["calls"] = []
    for c in calls:  # one record per target per line
        sig = (c["target"], c["line"])
        if sig not in seen:
            seen.add(sig)
            p["calls"].append(c)

    uses, seen_uses = [], set()
    for ref in p["type_refs"]:
        targets = idx.lookup(ref["type"], key)
        if len(targets) == 1 and targets[0] != key and targets[0] not in seen_uses:
            seen_uses.add(targets[0])
            uses.append({"target": targets[0], "line": ref["line"], "how": ref["how"]})
    p["uses"] = uses


def _call(target: str, c: dict, **extra) -> dict:
    return {"target": target, "dynamic": False, "args": [], "line": c["line"], "paragraph": c.get("paragraph"),
            **extra}


def _unresolved(c: dict, candidates: list[str], reason: str) -> dict:
    expr = f"{c.get('receiver') or ''}.{c['method']}".lstrip(".")
    return {"target": expr, "dynamic": True, "unresolved": True, "candidates": sorted(candidates),
            "reason": reason, "args": [], "line": c["line"], "paragraph": c.get("paragraph")}


def _resolve_call(key: str, p: dict, c: dict, idx: _Index) -> dict | None:
    method = c["method"]
    member = c.get("member_of")
    if member:  # _db.Customers.Where(...) -> EF DbSet on a DbContext defined in the project
        for ctx in idx.lookup(member["type"], key):
            entity = (idx.programs[ctx].get("dbsets") or {}).get(member["member"])
            if entity:
                table = idx.table_of(entity) or member["member"].upper()
                _table_access(p, table, method in EF_WRITES, c, f"{member['member']}.{method}")
                return None

    if method in SESSION_WRITES:  # session.add(Statement(...)) on an ORM entity
        for t in c.get("arg_types") or []:
            table = idx.table_of(t)
            if table:
                _table_access(p, table, True, c, f"{method}({t})")
                return None

    rtype = c.get("receiver_type")
    if rtype:
        targets = idx.lookup(rtype, key)
        if not targets:
            return None  # a library or framework type
        if len(targets) > 1:
            return _unresolved(c, targets, f"Several components are named {rtype}")
        return _dispatch(key, p, c, targets[0], idx)

    if c.get("receiver") is None:  # bare function call: a module-level function elsewhere in the project
        funcs = [k for k in idx.methods.get(method, []) if k != key and idx.programs[k]["kind"] == "module"
                 and idx.family(k) == idx.family(key)]
        return _call(funcs[0], c, inferred=True) if len(funcs) == 1 else None

    if not c.get("receiver_local"):
        return None  # e.g. requests.get(), os.path.join(): a module, not a project object
    candidates = [k for k in idx.methods.get(method, []) if k != key and idx.family(k) == idx.family(key)
                  and idx.programs[k]["kind"] != "module"]
    if len(candidates) == 1:
        return _call(candidates[0], c, inferred=True)
    if len(candidates) > 1:
        return _unresolved(c, candidates, f"The type of '{c['receiver']}' is not declared; "
                                          f"{len(candidates)} components define {method}()")
    return None


def _dispatch(key: str, p: dict, c: dict, target: str, idx: _Index) -> dict | None:
    t = idx.programs[target]
    method = c["method"]
    if t.get("repository_entity"):  # Spring Data repository: the call is table access
        table = idx.table_of(t["repository_entity"])
        if table:
            write = method.lower().startswith(WRITE_PREFIXES)
            _table_access(p, table, write, c, f"{target}.{method}")
            return None
    if t["kind"] == "interface":
        impls = [k for k in idx.implementers.get(target.split("@")[0], []) if k != key]
        defining = [k for k in impls if method in {x["name"] for x in idx.programs[k]["paragraphs"]}] or impls
        if len(defining) == 1:
            return _call(defining[0], c, via=target)
        if len(defining) > 1:
            return _unresolved(c, defining, f"{target} has {len(defining)} implementations; "
                                            "the one used is chosen at runtime")
    return _call(target, c)


# ----------------------------------------------------------------------------- APIs


def _publish_code_apis(model: dict, modern: dict) -> None:
    for key, p in modern.items():
        if not p["endpoints"]:
            continue
        api_id = f"{key.split('@')[0]}_API"
        model["apis"][api_id] = {
            "id": api_id, "title": f"{key} endpoints", "version": None,
            "description": f"REST endpoints implemented in {p['path']} (discovered from code).",
            "operations": [{"method": e["method"], "path": e["path"], "operation_id": e["handler"], "summary": "",
                            "legacy_program": key, "line": e["line"], "parameters": e["params"]}
                           for e in p["endpoints"]],
            "backing_programs": [key], "line": p["endpoints"][0]["line"], "path": p["path"],
            "documented": False, "source": "code",
        }


def normalize_path(path: str) -> str:
    """'/api/customers/{id}', '/api/customers/:customerId' and '<int:id>' all become '/api/customers/{}'."""
    path = re.sub(r"\{[^}]*\}|:\w+|<[^>]*>", "{}", path or "/")
    return "/" + "/".join(s for s in path.split("/") if s)


def _link_spec_operations(model: dict) -> None:
    code_ops = {}
    for api in model["apis"].values():
        if api.get("source") == "code":
            for o in api["operations"]:
                code_ops[(o["method"], normalize_path(o["path"]))] = o["legacy_program"]
    for api in model["apis"].values():
        if api.get("source") == "code":
            continue
        for o in api["operations"]:
            impl = code_ops.get((o["method"], normalize_path(o["path"])))
            if impl:
                o.setdefault("implemented_by", impl)
                if impl not in api["backing_programs"]:
                    api["backing_programs"].append(impl)


# ----------------------------------------------------------------------------- shell jobs


def _resolve_jobs(model: dict) -> None:
    programs = model["programs"]
    for job in model["jobs"].values():
        if job.get("kind") != "shell":
            continue
        for step in job["steps"]:
            inv = step.get("invokes") or {}
            kind, target = inv.get("kind"), inv.get("target") or ""
            found = None
            if kind in {"python", "node"}:
                found = _program_for_script(programs, target, kind)
            elif kind == "python-module":
                found = _program_for_script(programs, target.replace(".", "/") + ".py", "python")
            elif kind == "java":
                name = target.split(".")[-1]
                matches = [k for k, p in programs.items() if k.split("@")[0] == name and p.get("language") == "java"]
                found = matches[0] if len(matches) == 1 else None
            elif kind == "shell":
                stem = PurePosixPath(target).stem
                if stem in model["jobs"] and stem != job["name"]:
                    step["calls"].append({"target": stem, "kind": "job", "line": step["line"]})
                    continue
            elif kind == "sql":
                routines = [r for r in model["routines"].values() if _same_file(r["path"], target)]
                for r in routines:
                    step["calls"].append({"target": r["name"], "kind": "procedure", "line": step["line"]})
                if routines:
                    continue
            elif kind == "http":
                step["calls"].append({"target": target, "kind": "http", "line": step["line"]})
                continue
            if found:
                step["program"] = found
            else:
                step["unresolved"] = step.get("command") or target


def _same_file(path: str, target: str) -> bool:
    a, b = PurePosixPath(path.replace("\\", "/")), PurePosixPath(target.replace("\\", "/").lstrip("./"))
    return a.as_posix().endswith(b.as_posix()) or b.as_posix().endswith(a.as_posix())


def _program_for_script(programs: dict, target: str, kind: str) -> str | None:
    langs = {"python": {"python"}, "node": {"javascript", "typescript"}}[kind]
    in_file = [k for k, p in programs.items() if p.get("language") in langs and _same_file(p["path"], target)]
    if not in_file and kind == "node":  # dist/jobs/notify.js -> src/jobs/notify.ts
        stem = PurePosixPath(target).stem
        in_file = [k for k, p in programs.items() if p.get("language") in langs and PurePosixPath(p["path"]).stem == stem]
    if not in_file:
        return None
    entry = [k for k in in_file if programs[k].get("entry_point") or programs[k]["kind"] == "module"]
    choice = entry or in_file
    return choice[0] if len(choice) == 1 else None


# ----------------------------------------------------------------------------- COBOL


def _flag_dynamic_cobol_calls(model: dict) -> None:
    """CALL WS-PGM-NAME whose value is never a literal: the target is decided at runtime."""
    programs = model["programs"]
    for p in programs.values():
        if p.get("language") not in COBOL_LANGUAGES:
            continue
        for c in p["calls"]:
            if c["dynamic"] and c["target"] not in programs:
                c["unresolved"] = True
                c.setdefault("candidates", [])
                c.setdefault("reason", f"Program name is held in variable {c['target']} and set at runtime")
