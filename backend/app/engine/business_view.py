"""File-level map of an application for people who do not read code.

Every source file becomes one box; databases, data files and outside systems become their own boxes. The
component graph is rolled up to file level and every link carries a plain sentence ("PAYMENT.CBL saves
data to Payment ledger") with its file:line evidence. Each file gets a short description in business terms.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import PurePosixPath

from app.engine.plain import file_label, table_label

KIND_BY_LANGUAGE = {
    "cobol": "Program", "copybook": "Shared record layout", "jcl": "Batch job", "shell": "Batch script",
    "sql": "Database script", "api": "API contract", "config": "Configuration", "doc": "Document",
    "java": "Program", "csharp": "Program", "python": "Program", "javascript": "Program", "typescript": "Program",
}
VERB = {  # how each technical relationship reads in a sentence, from the file's point of view
    "CALLS": "calls", "EXECUTES": "runs", "INCLUDED_IN": "is shared by", "EXPOSED_AS": "is published as",
    "USES": "uses", "UNRESOLVED_CALL": "may call (decided at runtime)", "CONFIGURES": "connects to",
    "MAPS_TO": "describes the data in", "WRITES": "saves data to", "READ_BY": "reads data from",
    "REFERENCES": "refers to", "DEFINES": "defines",
}


def build_file_view(model: dict, graph: dict, insights: dict, rules: list[dict]) -> dict:
    files_meta = model["files"]
    node_file: dict[str, str] = {}
    for n in graph["nodes"]:
        if n.get("file") and n["type"] in {"program", "job", "copybook", "api", "procedure", "config"}:
            node_file[n["id"]] = n["file"]
    nodes_by_id = {n["id"]: n for n in graph["nodes"]}

    programs_in: dict[str, list[str]] = defaultdict(list)
    for name, p in model["programs"].items():
        programs_in[p["path"]].append(name)
    stats = insights.get("programs", {})
    capability_of = {p: c["name"] for c in insights.get("capabilities", []) for p in c["programs"]}
    rules_in = defaultdict(int)
    for r in rules:
        if not r["is_technical"]:
            rules_in[r["file"]] += 1

    def endpoint(node_id: str) -> str | None:
        """Box id for a graph node: its file, or a data / outside-system box."""
        if node_id in node_file:
            return f"file:{node_file[node_id]}"
        t = node_id.split(":", 1)[0]
        if t == "column":
            return f"data:{nodes_by_id.get(node_id, {}).get('table') or node_id.split(':', 1)[1].split('.')[0]}"
        if t == "table":
            return f"data:{node_id.split(':', 1)[1]}"
        if t == "dataset":
            return f"datafile:{node_id.split(':', 1)[1]}"
        if t in {"external", "unresolved"}:
            return f"{t}:{node_id.split(':', 1)[1]}"
        if t == "program":  # referenced but not uploaded
            return f"external:{node_id.split(':', 1)[1]}"
        return None

    links: dict[tuple[str, str, str], dict] = {}
    for e in graph["edges"]:
        if e["type"] in {"HAS_COLUMN"}:
            continue
        a, b = endpoint(e["source"]), endpoint(e["target"])
        etype = e["type"]
        if etype == "READ_BY":  # table -> program reads better as program reads table
            a, b = b, a
        if not a or not b or a == b:
            continue
        key = (a, b, etype)
        link = links.setdefault(key, {"source": a, "target": b, "type": etype, "verb": VERB.get(etype, etype.lower()),
                                      "count": 0, "evidence": []})
        link["count"] += 1
        for ev in e.get("evidence", [])[:3]:
            if len(link["evidence"]) < 5 and ev not in link["evidence"]:
                link["evidence"].append(ev)
    for n in graph["nodes"]:  # the SQL script that creates a table
        if n["type"] == "table" and n.get("file") and n.get("declared"):
            key = (f"file:{n['file']}", f"data:{n['label']}", "DEFINES")
            links.setdefault(key, {"source": key[0], "target": key[1], "type": "DEFINES", "verb": VERB["DEFINES"],
                                   "count": 1, "evidence": [{"file": n["file"], "line": n.get("line")}]})

    boxes: dict[str, dict] = {}
    for path, meta in files_meta.items():
        fid = f"file:{path}"
        comps = sorted(programs_in.get(path, []))
        lang = meta["language"]
        kind = KIND_BY_LANGUAGE.get(lang, "File")
        cap = next((capability_of[c] for c in comps if c in capability_of), None)
        purpose = next((model["programs"][c]["purpose"] for c in comps if model["programs"][c].get("purpose")), "")
        crit = max((stats.get(c, {}).get("criticality_score", 0) for c in comps), default=0)
        level = next((stats[c].get("criticality") for c in comps if c in stats and stats[c].get("criticality")), None)
        unused = bool(comps) and all(stats.get(c, {}).get("unused") for c in comps)
        boxes[fid] = {
            "id": fid, "kind": "file", "label": file_label(path), "path": path,
            "folder": str(PurePosixPath(path).parent) if "/" in path else "",
            "language": lang, "role": kind, "capability": "Unused" if unused else cap,
            "components": comps, "purpose": purpose, "business_rules": rules_in.get(path, 0),
            "lines": meta.get("line_count", 0), "criticality": "Unused" if unused else level, "score": crit,
        }
    for (a, b, _), link in links.items():
        for end in (a, b):
            if end in boxes:
                continue
            kind, name = end.split(":", 1)
            label = {"data": table_label(name), "datafile": file_label(name)}.get(kind, name)
            role = {"data": "Database table", "datafile": "Data file / report", "external": "Outside system",
                    "unresolved": "Decided at runtime"}[kind]
            boxes[end] = {"id": end, "kind": kind, "label": label, "name": name, "role": role, "path": None,
                          "folder": "", "language": None, "capability": None, "components": [], "purpose": "",
                          "business_rules": 0, "lines": 0, "criticality": None, "score": 0}

    out_links = list(links.values())
    for link in out_links:
        link["sentence"] = _sentence(boxes[link["source"]], boxes[link["target"]], link)
    for box in boxes.values():
        box["summary"] = _summary(box, out_links, boxes)
    order = {"file": 0, "data": 1, "datafile": 2, "external": 3, "unresolved": 4}
    return {
        "nodes": sorted(boxes.values(), key=lambda b: (order[b["kind"]], b.get("folder") or "", b["label"])),
        "links": sorted(out_links, key=lambda l: (l["source"], l["target"])),
        "legend": {k: v for k, v in VERB.items()},
    }


def _sentence(a: dict, b: dict, link: dict) -> str:
    times = f" ({link['count']} places)" if link["count"] > 1 else ""
    return f"{a['label']} {link['verb']} {b['label']}{times}."


def _summary(box: dict, links: list[dict], boxes: dict) -> str:
    outgoing = [l for l in links if l["source"] == box["id"]]
    incoming = [l for l in links if l["target"] == box["id"]]

    def names(ls, end):
        return sorted({boxes[l[end]]["label"] for l in ls})

    if box["kind"] == "file":
        role = box["role"].lower().replace("api ", "API ")
        article = "an" if role[:1] in "aeiou" else "a"
        parts = [f"{box['label']} is {article} {role}"
                 + (f" in {box['capability']}" if box.get("capability") and box["capability"] != "Unused" else "") + "."]
        if box["purpose"]:
            parts.append(re.split(r"(?<=[.!?])\s", box["purpose"], maxsplit=1)[0])
        defines = names([l for l in outgoing if l["type"] == "DEFINES"], "target")
        if defines:
            parts.append(f"It defines the tables {_list(defines)}.")
        reads = names([l for l in outgoing if l["type"] == "READ_BY"], "target")
        writes = names([l for l in outgoing if l["type"] == "WRITES"], "target")
        calls = names([l for l in outgoing if l["type"] in {"CALLS", "EXECUTES"}], "target")
        users = names([l for l in incoming if l["type"] in {"CALLS", "EXECUTES", "USES"}], "source")
        if reads:
            parts.append(f"It reads {_list(reads)}.")
        if writes:
            parts.append(f"It saves to {_list(writes)}.")
        if calls:
            parts.append(f"It hands work to {_list(calls)}.")
        if users:
            parts.append(f"It is started or used by {_list(users)}.")
        if box["business_rules"]:
            parts.append(f"It contains {box['business_rules']} business rule(s).")
        if box["criticality"] == "Unused":
            parts.append("Nothing in the uploaded code uses it; it may be safe to retire.")
        elif box["criticality"] in {"Critical", "High"}:
            parts.append(f"Criticality: {box['criticality'].lower()}. Changes here affect many other parts.")
        return " ".join(parts)
    readers = names([l for l in incoming if l["type"] == "READ_BY"], "source")
    writers = names([l for l in incoming if l["type"] == "WRITES"], "source")
    if box["kind"] in {"data", "datafile"}:
        what = "database table" if box["kind"] == "data" else "data file or report"
        s = f"{box['label']} is a {what}."
        if writers:
            s += f" Written by {_list(writers)}."
        if readers:
            s += f" Read by {_list(readers)}."
        return s
    if box["kind"] == "unresolved":
        return (f"{box['label']} is a call whose target is chosen while the system runs, so it cannot be "
                "confirmed from the code alone.")
    users = names(incoming, "source")
    return f"{box['label']} is outside the uploaded code" + (f"; used by {_list(users)}." if users else ".")


def _list(items: list[str], limit: int = 4) -> str:
    if len(items) > limit:
        return ", ".join(items[:limit]) + f" and {len(items) - limit} more"
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
