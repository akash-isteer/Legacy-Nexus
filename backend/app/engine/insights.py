"""Portfolio insights: criticality, unused components, business capabilities and the application map."""
from __future__ import annotations

import re
from collections import defaultdict

import networkx as nx

from app.engine.catalog import CAPABILITY_KEYWORDS


def to_networkx(graph: dict) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    for n in graph["nodes"]:
        g.add_node(n["id"], **n)
    for e in graph["edges"]:
        g.add_edge(e["source"], e["target"], key=e["type"], **e)
    return g


def _tokens(text: str) -> list[str]:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)  # AccountController -> Account Controller
    return [t for t in re.split(r"[^A-Z0-9]+", text.upper()) if t]


def capability_for(program: dict, tables: list[str]) -> str:
    sources = [_tokens(program["program_id"]), [t for name in tables for t in _tokens(name)], _tokens(program["purpose"])]
    for tokens in sources:
        for keywords, capability in CAPABILITY_KEYWORDS:
            if any(tok.startswith(k) for tok in tokens for k in keywords):
                return capability
    return "Uncategorised"


def compute_insights(model: dict, graph: dict, rules: list[dict]) -> dict:
    g = to_networkx(graph)
    programs = model["programs"]
    rules_by_program = defaultdict(lambda: {"business": 0, "technical": 0})
    for r in rules:
        rules_by_program[r["program"]]["technical" if r["is_technical"] else "business"] += 1

    def preds(node: str, etype: str) -> set[str]:
        return {u for u, _, k in g.in_edges(node, keys=True) if k == etype}

    def succs(node: str, etype: str) -> set[str]:
        return {v for _, v, k in g.out_edges(node, keys=True) if k == etype}

    call_graph = nx.DiGraph()
    for e in graph["edges"]:
        if e["type"] == "CALLS" and e["target"].startswith("program:"):
            call_graph.add_edge(e["source"], e["target"])

    # Components that an UNRESOLVED_CALL may reach at runtime are possibly used, never "unused".
    maybe_called = {c for n in graph["nodes"] if n["type"] == "unresolved" for c in n.get("candidates", [])}

    program_stats = {}
    for name, p in programs.items():
        pid = f"program:{name}"
        callers = {c for c in preds(pid, "CALLS") if c.startswith("program:")}
        jobs = preds(pid, "EXECUTES")
        apis = succs(pid, "EXPOSED_AS")
        written = {t for t in succs(pid, "WRITES") if t.startswith("table:")}
        read = {t for t in preds(pid, "READ_BY") if t.startswith("table:")}
        datasets_out = {t for t in succs(pid, "WRITES") if t.startswith("dataset:")}
        datasets_in = {t for t in preds(pid, "READ_BY") if t.startswith("dataset:")}
        callees = succs(pid, "CALLS")
        dependents = nx.ancestors(call_graph, pid) if pid in call_graph else set()
        br = rules_by_program[name]["business"]
        score = (3 * len(callers) + 2 * len(jobs) + 3 * len(apis) + 2 * len(written) + len(read)
                 + 0.5 * br + len(dependents) + len(datasets_out))
        reasons = []
        if callers:
            reasons.append(f"called by {len(callers)} program(s)")
        if dependents - callers:
            reasons.append(f"{len(dependents)} program(s) depend on it transitively")
        if jobs:
            reasons.append(f"runs in {len(jobs)} batch job(s)")
        if apis:
            reasons.append(f"backs {len(apis)} API(s)")
        if written:
            reasons.append(f"writes {len(written)} table(s)")
        if br:
            reasons.append(f"implements {br} business rule(s)")
        users = {u for u in preds(pid, "USES") if u.startswith("program:")}
        unused = (not callers and not jobs and not apis and not preds(pid, "CALLS") and not users
                  and not p.get("entry_point") and not p.get("entity_table") and name not in maybe_called)
        unresolved = len(succs(pid, "UNRESOLVED_CALL"))
        if unresolved:
            reasons.append(f"{unresolved} call(s) decided at runtime")
        program_stats[name] = {
            "program": name,
            "file": p["path"],
            "language": p.get("language", "cobol"),
            "type": p["program_type"],
            "used_by": sorted(u.split(":", 1)[1] for u in users),
            "unresolved_calls": unresolved,
            "purpose": p["purpose"],
            "loc": p["loc"],
            "complexity": p["metrics"].get("cyclomatic_complexity", 1),
            "paragraphs": p["metrics"].get("paragraph_count", 0),
            "fan_in": len(callers),
            "fan_out": len(callees),
            "callers": sorted(c.split(":", 1)[1] for c in callers),
            "callees": sorted(c.split(":", 1)[1] for c in callees),
            "jobs": sorted(j.split(":", 1)[1] for j in jobs),
            "apis": sorted(a.split(":", 1)[1] for a in apis),
            "tables_read": sorted(t.split(":", 1)[1] for t in read),
            "tables_written": sorted(t.split(":", 1)[1] for t in written),
            "datasets_in": sorted(d.split(":", 1)[1] for d in datasets_in),
            "datasets_out": sorted(d.split(":", 1)[1] for d in datasets_out),
            "business_rules": br,
            "technical_rules": rules_by_program[name]["technical"],
            "criticality_score": round(score, 1),
            "criticality_reasons": reasons,
            "unused": unused,
        }

    # Criticality levels relative to the portfolio
    scores = sorted((s["criticality_score"] for s in program_stats.values() if not s["unused"]), reverse=True)
    if scores:
        top = scores[max(0, len(scores) // 4 - 1)] if len(scores) >= 4 else scores[0]
        for s in program_stats.values():
            sc = s["criticality_score"]
            if s["unused"]:
                level = "Unused"
            elif sc >= max(top, 1) and sc >= 8:
                level = "Critical"
            elif sc >= 6:
                level = "High"
            elif sc >= 3:
                level = "Medium"
            else:
                level = "Low"
            s["criticality"] = level

    # Unused / low-use components
    unused_programs = {n for n, s in program_stats.items() if s["unused"]}
    unused_components = [
        {"type": "program", "name": n, "file": programs[n]["path"],
         "reason": "Not called, not run by any job, not exposed as an API" + (
             "" if programs[n].get("language", "cobol") == "cobol" else ", not referenced by other code")}
        for n in sorted(unused_programs)
    ]
    for node in graph["nodes"]:
        if node["type"] != "table":
            continue
        users = {v for _, v, k in g.out_edges(node["id"], keys=True) if k == "READ_BY"} | {
            u for u, _, k in g.in_edges(node["id"], keys=True) if k == "WRITES"}
        names = {u.split(":", 1)[1] for u in users if u.startswith(("program:", "procedure:"))}
        if not names:
            unused_components.append({"type": "table", "name": node["label"], "file": node.get("file"),
                                      "reason": "No program or procedure reads or writes it"})
        elif names <= unused_programs:
            unused_components.append({"type": "table", "name": node["label"], "file": node.get("file"),
                                      "reason": f"Only used by unused program(s): {', '.join(sorted(names))}"})
    for name, p in programs.items():
        if p.get("language", "cobol") != "cobol":
            continue  # public methods of classes are called from other files; only COBOL paragraphs are local
        performed = {x["to"] for x in p["performs"]}
        for i, para in enumerate(p["paragraphs"]):
            if i == 0 or para["name"] in performed or para.get("is_section"):
                continue
            if any(x.get("thru") for x in p["performs"]):
                continue
            unused_components.append({"type": "paragraph", "name": f"{name}.{para['name']}", "file": p["path"],
                                      "line": para["start_line"], "reason": "Never performed (dead code)"})
    for name, cb in model["copybooks"].items():
        if not any(c["name"] == name for p in programs.values() for c in p["copybooks"]):
            unused_components.append({"type": "copybook", "name": name, "file": cb["path"], "reason": "Not included by any program"})

    capabilities = _capabilities(model, graph, program_stats)

    db_dependencies = sum(
        1 for e in graph["edges"]
        if e["type"] in {"READ_BY", "WRITES"} and (e["source"].startswith(("table:", "column:")) or e["target"].startswith(("table:", "column:")))
    )
    externals = [n for n in graph["nodes"] if n["type"] in {"api", "external"}]
    overview = {
        "programs_analysed": len(programs),
        "copybooks": len(model["copybooks"]),
        "jobs": len(model["jobs"]),
        "tables": sum(1 for n in graph["nodes"] if n["type"] == "table"),
        "procedures": len(model["routines"]),
        "datasets": sum(1 for n in graph["nodes"] if n["type"] == "dataset"),
        "critical_programs": sum(1 for s in program_stats.values() if s.get("criticality") == "Critical"),
        "unused_components": len(unused_components),
        "db_dependencies": db_dependencies,
        "business_rules": sum(1 for r in rules if not r["is_technical"]),
        "technical_rules": sum(1 for r in rules if r["is_technical"]),
        "external_interfaces": len(externals),
        "lines_of_code": sum(p["loc"] for p in programs.values()),
        "unresolved_calls": sum(1 for e in graph["edges"] if e["type"] == "UNRESOLVED_CALL"),
        "configs": len(model.get("configs", [])),
        "warnings": model["warnings"],
    }
    return {
        "programs": program_stats,
        "unused_components": unused_components,
        "capabilities": capabilities["capabilities"],
        "application_map": capabilities["map"],
        "external_interfaces": [
            {"id": n["id"], "name": n["label"], "type": n["type"], "documented": bool(n.get("documented")),
             "description": n.get("description") or n.get("title") or "",
             "used_by": sorted({e["source"].split(":", 1)[1] for e in graph["edges"] if e["target"] == n["id"]} |
                               {e["target"].split(":", 1)[1] for e in graph["edges"] if e["source"] == n["id"] and e["type"] == "CALLS"})}
            for n in externals
        ],
        "overview": overview,
    }


def _capabilities(model: dict, graph: dict, program_stats: dict) -> dict:
    cap_of: dict[str, str] = {}
    for name, p in model["programs"].items():
        s = program_stats[name]
        cap_of[name] = capability_for(p, s["tables_written"] + s["tables_read"])

    caps: dict[str, dict] = defaultdict(lambda: {"programs": [], "tables": set(), "jobs": set(), "apis": set()})
    for name, cap in cap_of.items():
        s = program_stats[name]
        caps[cap]["programs"].append(name)
        caps[cap]["tables"].update(s["tables_written"])
        caps[cap]["jobs"].update(s["jobs"])
        caps[cap]["apis"].update(s["apis"])

    def add_acyclic(dag: nx.DiGraph, a: str | None, b: str | None) -> None:
        if a and b and a != b and not (dag.has_node(b) and dag.has_node(a) and nx.has_path(dag, b, a)):
            dag.add_edge(a, b)

    dag = nx.DiGraph()
    dag.add_nodes_from(caps)
    # 1. Service dependencies first: the provider (callee) comes before its consumers (callers)
    for e in graph["edges"]:
        src, dst = e["source"], e["target"]
        if e["type"] == "CALLS" and src.startswith("program:") and dst.startswith("program:"):
            add_acyclic(dag, cap_of.get(dst.split(":", 1)[1]), cap_of.get(src.split(":", 1)[1]))
    # 2. Then data hand-offs: the writer of a table comes before its readers
    writers_of: dict[str, set[str]] = defaultdict(set)
    for e in graph["edges"]:
        if e["type"] == "WRITES" and e["source"].startswith("program:") and e["target"].startswith(("table:", "dataset:")):
            writers_of[e["target"]].add(e["source"].split(":", 1)[1])
    for e in graph["edges"]:
        if e["type"] == "READ_BY" and e["target"].startswith("program:"):
            reader = cap_of.get(e["target"].split(":", 1)[1])
            for w in writers_of.get(e["source"], ()):
                add_acyclic(dag, cap_of.get(w), reader)

    unused_caps = {c for c, v in caps.items() if all(program_stats[p]["unused"] for p in v["programs"])}
    order = list(nx.lexicographical_topological_sort(dag, key=lambda c: (c in unused_caps, c == "Uncategorised", c)))
    order = [c for c in order if c not in unused_caps] + [c for c in order if c in unused_caps]

    capability_list = []
    for cap in order:
        c = caps[cap]
        capability_list.append({
            "name": cap,
            "programs": sorted(c["programs"]),
            "tables": sorted(c["tables"]),
            "jobs": sorted(c["jobs"]),
            "apis": sorted(c["apis"]),
            "business_rules": sum(program_stats[p]["business_rules"] for p in c["programs"]),
            "unused": all(program_stats[p]["unused"] for p in c["programs"]),
        })
    return {"capabilities": capability_list, "map": {
        "order": order,
        "edges": [{"source": a, "target": b} for a, b in dag.edges()],
    }}
