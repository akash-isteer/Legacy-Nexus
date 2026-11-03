"""Legacy Knowledge Graph (component level) and field-level lineage graph.

Component graph edges follow the direction of control/data flow so that a path reads like the
business process: JOB -> PROGRAM -> PROGRAM -> TABLE -> PROGRAM -> DATASET.

Edge types
  EXECUTES      job -> program | procedure
  CALLS         program -> program | external
  WRITES        program|procedure -> table | column | dataset
  READ_BY       table | column | dataset -> program | procedure
  HAS_COLUMN    table -> column
  REFERENCES    table -> table (foreign key)
  INCLUDED_IN   copybook -> program
  EXPOSED_AS    program -> api
  UNRESOLVED_CALL program | job -> unresolved   (target decided at runtime; candidates listed, never guessed)
  USES          program -> program   (type dependency: field, parameter, new, extends/implements)
  MAPS_TO       program -> table     (ORM entity mapped to a table)
  CONFIGURES    config -> external   (database, broker, HTTP service named in configuration)
"""
from __future__ import annotations

import re
from collections import defaultdict

from app.engine.catalog import KNOWN_EXTERNAL_INTERFACES
from app.parsers.cobol import identifiers
from app.parsers.jcl import dd_direction


class GraphBuilder:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple[str, str, str], dict] = {}

    def node(self, node_id: str, type_: str, label: str, **meta) -> str:
        existing = self.nodes.get(node_id)
        if existing:
            for k, v in meta.items():
                if v is not None and existing.get(k) in (None, "", [], {}):
                    existing[k] = v
        else:
            self.nodes[node_id] = {"id": node_id, "type": type_, "label": label, **meta}
        return node_id

    def edge(self, source: str, target: str, type_: str, file: str | None = None, line: int | None = None,
             **meta) -> None:
        key = (source, target, type_)
        e = self.edges.get(key)
        if e is None:
            e = self.edges[key] = {"source": source, "target": target, "type": type_, "evidence": [], **meta}
        if file and line and len(e["evidence"]) < 20:
            ev = {"file": file, "line": line}
            if ev not in e["evidence"]:
                e["evidence"].append(ev)

    def to_dict(self) -> dict:
        return {"nodes": list(self.nodes.values()), "edges": list(self.edges.values())}


def program_node_id(name: str) -> str:
    return f"program:{name}"


def build_component_graph(model: dict) -> dict:
    g = GraphBuilder()
    programs = model["programs"]

    for name, t in model["tables"].items():
        g.node(f"table:{name}", "table", name, file=t["path"], line=t["line"],
               columns=[c["name"] for c in t["columns"]], declared=True)
        for c in t["columns"]:
            cid = g.node(f"column:{name}.{c['name']}", "column", f"{name}.{c['name']}", file=t["path"],
                         line=c["line"], table=name, data_type=c["type"])
            g.edge(f"table:{name}", cid, "HAS_COLUMN", t["path"], c["line"])
        for fk in t["foreign_keys"]:
            g.edge(f"table:{name}", f"table:{fk['ref_table']}", "REFERENCES", t["path"], t["line"],
                   column=fk["column"])

    def table_node(name: str) -> str:
        return g.node(f"table:{name}", "table", name, declared=name in model["tables"])

    def column_node(table: str | None, col: str) -> str | None:
        if not table or col == "*":
            return None
        cid = g.node(f"column:{table}.{col}", "column", f"{table}.{col}", table=table)
        g.edge(table_node(table), cid, "HAS_COLUMN")
        return cid

    def sql_edges(owner: str, st: dict, file: str, line: int, cursors: dict | None = None) -> None:
        reads = list(st.get("tables_read", []))
        read_cols = [tuple(c) for c in st.get("columns_read", [])] + [tuple(c) for c in st.get("select_columns", [])]
        if st.get("op") == "FETCH" and cursors and st.get("cursor") in cursors:
            decl = cursors[st["cursor"]]
            reads += decl.get("tables_read", [])
            read_cols += [tuple(c) for c in decl.get("columns_read", [])]
        for t in reads:
            g.edge(table_node(t), owner, "READ_BY", file, line)
        for t in st.get("tables_written", []):
            g.edge(owner, table_node(t), "WRITES", file, line)
        for t, c in dict.fromkeys(read_cols):
            cid = column_node(t, c)
            if cid:
                g.edge(cid, owner, "READ_BY", file, line)
        for t, c in dict.fromkeys(tuple(c) for c in st.get("columns_written", [])):
            cid = column_node(t, c)
            if cid:
                g.edge(owner, cid, "WRITES", file, line)

    def unresolved(source: str, call: dict, file: str) -> None:
        uid = g.node(f"unresolved:{call['target']}", "unresolved", call["target"],
                     candidates=call.get("candidates") or [], reason=call.get("reason", ""))
        g.edge(source, uid, "UNRESOLVED_CALL", file, call["line"], candidates=call.get("candidates") or [])

    # Programs
    for name, p in programs.items():
        g.node(program_node_id(name), "program", name, file=p["path"], line=p.get("start_line", 1),
               purpose=p["purpose"], program_type=p["program_type"], loc=p["loc"],
               language=p.get("language", "cobol"), kind=p.get("kind", "program"))
    for name, p in programs.items():
        pid = program_node_id(name)
        for cb in p["copybooks"]:
            cb_id = g.node(f"copybook:{cb['name']}", "copybook", cb["name"],
                           file=model["copybooks"].get(cb["name"], {}).get("path"))
            g.edge(cb_id, pid, "INCLUDED_IN", p["path"], cb["line"])
        for use in p.get("uses", []):
            if use["target"] in programs:
                g.edge(pid, program_node_id(use["target"]), "USES", p["path"], use["line"], how=use["how"])
        if p.get("entity_table"):
            g.edge(pid, table_node(p["entity_table"]), "MAPS_TO", p["path"], p.get("start_line", 1))
        for call in p["calls"]:
            target = call["target"]
            if call.get("unresolved"):
                unresolved(pid, call, p["path"])
            elif target in programs:
                g.edge(pid, program_node_id(target), "CALLS", p["path"], call["line"], dynamic=call["dynamic"])
            else:
                ext = g.node(f"external:{target}", "external", target,
                             description=KNOWN_EXTERNAL_INTERFACES.get(target, "Called program not found in the uploaded sources"))
                g.edge(pid, ext, "CALLS", p["path"], call["line"], dynamic=call["dynamic"])
        for blk in p["cics"]:
            tgt = blk.get("target_program")
            if tgt:
                tid = program_node_id(tgt) if tgt in programs else g.node(f"external:{tgt}", "external", tgt)
                g.edge(pid, tid, "CALLS", p["path"], blk["start_line"], via=f"CICS {blk['command']}")
        cursors = {b["analysis"]["cursor"]: b["analysis"] for b in p["sql_blocks"] if b["analysis"]["op"] == "DECLARE"}
        for blk in p["sql_blocks"]:
            if blk["analysis"]["op"] in {"INCLUDE", "OPEN", "CLOSE"}:
                continue
            if blk["analysis"]["op"] == "CALL" and blk["analysis"].get("called_procedure"):
                proc = blk["analysis"]["called_procedure"]
                g.edge(pid, g.node(f"procedure:{proc}", "procedure", proc), "CALLS", p["path"], blk["start_line"])
                continue
            sql_edges(pid, blk["analysis"], p["path"], blk["start_line"], cursors)

    # Stored procedures
    for name, r in model["routines"].items():
        rid = g.node(f"procedure:{name}", "procedure", name, file=r["path"], line=r["line"], kind=r["kind"])
        for st in r["statements"]:
            sql_edges(rid, st, r["path"], st["line"])

    # Jobs and datasets
    for name, job in model["jobs"].items():
        jid = g.node(f"job:{name}", "job", name, file=job["path"], line=job["line"], description=job["description"])
        for step in job["steps"]:
            runner = None
            if step["program"] and not step["is_system_program"]:
                runner = program_node_id(step["program"])
                if step["program"] not in programs:
                    g.node(runner, "program", step["program"], missing=True)
                g.edge(jid, runner, "EXECUTES", job["path"], step["line"], step=step["name"])
            for call in step["calls"]:
                if call["kind"] == "http":
                    target = g.node(f"external:{call['target']}", "external", call["target"],
                                    description="HTTP endpoint called by a script")
                    g.edge(jid, target, "CALLS", job["path"], call["line"], step=step["name"])
                    continue
                kind = call["kind"] if call["kind"] in {"procedure", "job"} else "program"
                target = g.node(f"{kind}:{call['target']}", kind, call["target"])
                g.edge(jid, target, "EXECUTES", job["path"], call["line"], step=step["name"],
                       via=step["program"])
            if step.get("unresolved"):
                unresolved(jid, {"target": step["unresolved"], "line": step["line"], "candidates": [],
                                 "reason": "The script starts something that is not in the uploaded code"}, job["path"])
            prog = programs.get(step["program"] or "")
            assigns = {f["assign"]: f for f in (prog or {}).get("files", [])}
            for dd in step["dds"]:
                if not dd["dsn"] or not runner:
                    continue
                ds = g.node(f"dataset:{dd['dsn']}", "dataset", dd["dsn"])
                f = assigns.get(dd["name"])
                modes = set(f["modes"]) if f else set()
                direction = "both" if "I-O" in modes else (
                    "output" if modes & {"OUTPUT", "EXTEND"} else ("input" if "INPUT" in modes else dd_direction(dd["disp"]))
                )
                if direction in ("input", "both"):
                    g.edge(ds, runner, "READ_BY", job["path"], dd["line"], dd=dd["name"])
                if direction in ("output", "both"):
                    g.edge(runner, ds, "WRITES", job["path"], dd["line"], dd=dd["name"])

    # APIs
    for api_id, api in model["apis"].items():
        aid = g.node(f"api:{api_id}", "api", api_id, file=api["path"], line=api["line"], title=api["title"],
                     documented=api.get("documented", True), source=api.get("source", "spec"),
                     operations=[f"{o['method']} {o['path']}" for o in api["operations"]])
        for prog in api["backing_programs"]:
            pid = program_node_id(prog)
            if prog not in programs:
                g.node(pid, "program", prog, missing=True)
            g.edge(pid, aid, "EXPOSED_AS", api["path"], api["line"])

    # Configuration: what the application connects to
    for cfg in model.get("configs", []):
        if not cfg.get("endpoints") and not cfg.get("destinations"):
            continue
        cid = g.node(f"config:{cfg['path']}", "config", cfg["path"].split("/")[-1], file=cfg["path"], line=1,
                     kind=cfg.get("kind"), app_name=cfg.get("app_name"))
        for ep in cfg.get("endpoints", []):
            label = f"{ep['host']}:{ep['port']}" if ep.get("port") else ep["host"]
            xid = g.node(f"external:{label}", "external", label,
                         description=f"{ep['kind'].replace('-', ' ').capitalize()} ({ep.get('scheme') or 'host'}) "
                                     f"configured at {ep['key']}", interface_kind=ep["kind"])
            g.edge(cid, xid, "CONFIGURES", cfg["path"], ep["line"], setting=ep["key"])
        for dest in cfg.get("destinations", []):
            xid = g.node(f"external:{dest['name']}", "external", dest["name"],
                         description=f"Message destination configured at {dest['key']}", interface_kind="messaging")
            g.edge(cid, xid, "CONFIGURES", cfg["path"], dest["line"], setting=dest["key"])

    return g.to_dict()


# ============================================================================ field-level lineage

def _fid(program: str, name: str) -> str:
    return f"field:{program}.{name}"


def build_lineage(model: dict, component_graph: dict) -> dict:
    """Field-level data and control flow across programs, tables, datasets and APIs."""
    programs = model["programs"]
    g = GraphBuilder()

    def data_names(p: dict) -> dict[str, dict]:
        items = {d["name"]: d for d in p["data_items"]}
        for cb in p["copybooks"]:
            for d in model["copybooks"].get(cb["name"], {}).get("data_items", []):
                items.setdefault(d["name"], {**d, "copybook": cb["name"]})
        return items

    names_by_prog = {name: data_names(p) for name, p in programs.items()}

    def field(prog: str, name: str) -> str | None:
        items = names_by_prog[prog]
        p = programs[prog]
        if name in p["conditions"]:
            name = p["conditions"][name]["parent"] or name
        item = items.get(name)
        if not item:
            return None
        return g.node(_fid(prog, name), "field", name, program=prog, file=p["path"], line=item["line"],
                      pic=item.get("pic"), section=item.get("section"))

    def column(table: str | None, col: str) -> str | None:
        if not table or col == "*":
            return None
        t = model["tables"].get(table)
        line = next((c["line"] for c in (t or {}).get("columns", []) if c["name"] == col), None)
        return g.node(f"column:{table}.{col}", "column", f"{table}.{col}", table=table,
                      file=(t or {}).get("path"), line=line)

    # Group structure (child -> parent), used to carry data from a field into its record
    for prog, items in names_by_prog.items():
        for item in items.values():
            if item.get("parent") and item["parent"] in items:
                child, parent = field(prog, item["name"]), field(prog, item["parent"])
                if child and parent:
                    g.edge(child, parent, "PART_OF", programs[prog]["path"], item["line"])

    for prog, p in programs.items():
        path = p["path"]
        # MOVE / COMPUTE / arithmetic
        for flow in p["data_flows"]:
            dst = field(prog, flow["dst"])
            src = field(prog, flow["src"]) if flow["src"] else None
            if dst and src and src != dst:
                g.edge(src, dst, "DATA", path, flow["line"], how=flow["kind"])

        # Embedded SQL
        cursors = {b["analysis"]["cursor"]: b["analysis"] for b in p["sql_blocks"] if b["analysis"]["op"] == "DECLARE"}
        for blk in p["sql_blocks"]:
            st = blk["analysis"]
            line = blk["start_line"]
            source = st
            if st["op"] == "FETCH" and st.get("cursor") in cursors:
                source = cursors[st["cursor"]]
            if st.get("into_vars"):
                targets = [field(prog, v) for v in st["into_vars"]]
                for (t, c), tgt in zip(source.get("select_columns", []), targets):
                    col = column(t, c)
                    if col and tgt:
                        g.edge(col, tgt, "DATA", path, line, how="sql select")
                filters = [column(t, c) for t, c in source.get("filter_columns", [])]
                keys = [field(prog, v) for v in source.get("input_vars", [])]
                for ctl in filter(None, filters + keys):
                    for tgt in filter(None, targets):
                        g.edge(ctl, tgt, "CONTROL", path, line, how="sql filter")
            for t, c, var in st.get("assignments", []):
                src, col = field(prog, var), column(t, c)
                if src and col:
                    g.edge(src, col, "DATA", path, line, how=f"sql {st['op'].lower()}")

        # Files: WRITE record -> dataset, dataset -> record on READ
        datasets = _program_datasets(model, prog)
        record_file = {r: f for f in p["files"] for r in f["records"]}
        for op in p["file_ops"]:
            if op["op"] in {"WRITE", "REWRITE"} and op["target"] in record_file:
                ds = datasets.get(record_file[op["target"]]["assign"])
                rec = field(prog, op["target"])
                if ds and rec:
                    g.edge(rec, g.node(ds, "dataset", ds.split(":", 1)[1]), "DATA", path, op["line"], how="write")
            if op["op"] == "READ":
                f = next((x for x in p["files"] if x["name"] == op["target"]), None)
                ds = datasets.get(f["assign"]) if f else None
                if ds:
                    for r in f["records"]:
                        rec = field(prog, r)
                        if rec:
                            g.edge(g.node(ds, "dataset", ds.split(":", 1)[1]), rec, "DATA", path, op["line"], how="read")
                            for child in names_by_prog[prog].values():
                                if child.get("parent") == r:
                                    cid = field(prog, child["name"])
                                    if cid:
                                        g.edge(rec, cid, "DATA", path, op["line"], how="record field")

        # Control dependence: everything assigned under a decision depends on its condition
        _control_edges(g, prog, p, field, column)

    # CALL ... USING: map caller arguments to callee parameters (direction from callee usage)
    for prog, p in programs.items():
        for call in p["calls"]:
            callee = programs.get(call["target"])
            if not callee:
                continue
            written, read = _param_usage(callee, names_by_prog[call["target"]])
            for arg, param in zip(call["args"], callee["using_params"]):
                _link_params(g, prog, arg, call["target"], param, call["line"], p["path"], field,
                             names_by_prog, written, read)

    # Program outputs exposed through APIs
    for api_id, api in model["apis"].items():
        aid = g.node(f"api:{api_id}", "api", api_id, file=api["path"], line=api["line"])
        for prog in api["backing_programs"]:
            callee = programs.get(prog)
            if not callee:
                continue
            written, _ = _param_usage(callee, names_by_prog[prog])
            for param in callee["using_params"]:
                for name in [param] + [c["name"] for c in names_by_prog[prog].values() if c.get("parent") == param]:
                    if name in written:
                        fid = field(prog, name)
                        if fid:
                            g.edge(fid, aid, "DATA", api["path"], api["line"], how="api response")

    # Stored procedures: filter columns control written columns
    for name, r in model["routines"].items():
        for st in r["statements"]:
            written = [column(t, c) for t, c in st.get("columns_written", [])]
            for t, c in st.get("filter_columns", []):
                src = column(t, c)
                for w in filter(None, written):
                    if src and src != w:
                        g.edge(src, w, "CONTROL", r["path"], st["line"], how=f"procedure {name}")

    return g.to_dict()


def _program_datasets(model: dict, prog: str) -> dict[str, str]:
    """ddname -> dataset node id, from the JCL steps that run this program."""
    out = {}
    for job in model["jobs"].values():
        for step in job["steps"]:
            if step["program"] == prog:
                for dd in step["dds"]:
                    if dd["dsn"]:
                        out[dd["name"]] = f"dataset:{dd['dsn']}"
    return out


def _control_edges(g: GraphBuilder, prog: str, p: dict, field, column) -> None:
    para_ranges = {x["name"]: (x["start_line"], x["end_line"]) for x in p["paragraphs"]}
    performs_by_para = defaultdict(list)
    for perf in p["performs"]:
        performs_by_para[perf["from"]].append(perf)

    def lines_in_paragraphs(names: set[str]) -> list[tuple[int, int]]:
        return [para_ranges[n] for n in names if n in para_ranges]

    def reachable_paragraphs(start: int, end: int) -> set[str]:
        direct = {perf["to"] for perf in p["performs"] if start <= perf["line"] <= end}
        seen: set[str] = set()
        stack = list(direct)
        while stack:
            name = stack.pop()
            if name in seen:
                continue
            seen.add(name)
            for perf in performs_by_para.get(name, []):
                stack.append(perf["to"])
        return seen

    for d in p["decisions"]:
        if d["type"] == "IF":
            cond_text = d["condition"]
        else:
            cond_text = d["subject"] + " " + " ".join(w["value"] for w in d["whens"])
        cond_fields = [f for f in (field(prog, n) for n in identifiers(cond_text)) if f]
        if not cond_fields:
            continue
        ranges = [(d["start_line"], d["end_line"])] + lines_in_paragraphs(reachable_paragraphs(d["start_line"], d["end_line"]))

        def inside(line: int) -> bool:
            return any(a <= line <= b for a, b in ranges)

        targets: set[str] = set()
        for flow in p["data_flows"]:
            if inside(flow["line"]):
                t = field(prog, flow["dst"])
                if t:
                    targets.add(t)
        for blk in p["sql_blocks"]:
            if inside(blk["start_line"]):
                for t, c in blk["analysis"].get("columns_written", []):
                    col = column(t, c)
                    if col:
                        targets.add(col)
        for op in p["file_ops"]:
            if op["op"] in {"WRITE", "REWRITE"} and inside(op["line"]):
                t = field(prog, op["target"])
                if t:
                    targets.add(t)
        for src in cond_fields:
            for tgt in targets:
                if src != tgt:
                    g.edge(src, tgt, "CONTROL", p["path"], d["start_line"], how="decision")


def _param_usage(callee: dict, items: dict[str, dict]) -> tuple[set[str], set[str]]:
    """Which linkage items the callee writes (outputs) and reads (inputs)."""
    written, read = set(), set()
    for flow in callee["data_flows"]:
        written.add(flow["dst"])
        if flow["src"]:
            read.add(flow["src"])
    for blk in callee["sql_blocks"]:
        written.update(blk["analysis"].get("into_vars", []))
        read.update(blk["analysis"].get("input_vars", []))
    for d in callee["decisions"]:
        read.update(identifiers(d.get("condition", "") or d.get("subject", "")))
    # a group is written/read when any of its children is
    for name, item in items.items():
        parent = item.get("parent")
        if parent and name in written:
            written.add(parent)
        if parent and name in read:
            read.add(parent)
    return written, read


def _link_params(g, caller, arg, callee, param, line, path, field, names_by_prog, written, read) -> None:
    pairs = [(arg, param)]
    caller_children = [c["name"] for c in names_by_prog[caller].values() if c.get("parent") == arg]
    callee_children = [c["name"] for c in names_by_prog[callee].values() if c.get("parent") == param]
    if caller_children and len(caller_children) == len(callee_children):
        pairs += list(zip(caller_children, callee_children))
    for a, prm in pairs:
        fa, fp = field(caller, a), field(callee, prm)
        if not fa or not fp:
            continue
        if prm in written:
            g.edge(fp, fa, "DATA", path, line, how=f"returned by CALL {callee}")
        if prm in read or prm not in written:
            g.edge(fa, fp, "DATA", path, line, how=f"passed to CALL {callee}")


def normalize_symbol(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())
