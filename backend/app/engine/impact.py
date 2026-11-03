"""Impact analysis: what can break if a column, field, table, program or dataset changes.

For data elements (columns, fields, datasets) the change is propagated through the field-level lineage graph,
following both data flow (MOVE, COMPUTE, SQL, CALL parameters, file I/O) and control dependence (values
decided by a condition on the changed element). For structural elements (programs, copybooks, jobs) the
component graph is used: callers, jobs, APIs and the data stores they write. Every hop keeps its evidence.
"""
from __future__ import annotations

from collections import defaultdict, deque

COMPONENT_TYPES = {"program", "table", "column", "dataset", "api", "job", "procedure", "external", "copybook"}


def _component(node_id: str) -> str:
    if node_id.startswith("field:"):
        return "program:" + node_id[6:].split(".", 1)[0]
    return node_id


def _label(node_id: str) -> str:
    return node_id.split(":", 1)[1]


def _type(node_id: str) -> str:
    return node_id.split(":", 1)[0]


def _severity(distance: int, impact_type: str, ctype: str) -> str:
    if distance <= 1:
        return "High"
    if ctype in {"api", "dataset", "table", "column"} and distance <= 4:
        return "High" if impact_type == "data" else "Medium"
    if distance <= 3:
        return "Medium" if impact_type != "data" else "High"
    return "Low" if impact_type == "control" else "Medium"


def analyze_impact(start_ids: list[str], graph: dict, lineage: dict, max_hops: int = 60) -> dict:
    lineage_ids = {n["id"] for n in lineage["nodes"]}
    graph_nodes = {n["id"]: n for n in graph["nodes"]}
    start_types = {_type(s) for s in start_ids}
    data_starts: list[str] = []
    for s in start_ids:
        if s.startswith("table:"):
            data_starts += [n["id"] for n in lineage["nodes"] if n["id"].startswith(f"column:{s[6:]}.")]
        elif s in lineage_ids:
            data_starts.append(s)

    results: dict[str, dict] = {}
    if data_starts:
        _taint(data_starts, lineage, results, max_hops)
    if start_types & {"program", "copybook", "job", "procedure", "external", "api", "table", "dataset"}:
        _structural(start_ids, graph, results)

    # Jobs and APIs that run/expose impacted programs
    exec_edges = defaultdict(list)
    exposed = defaultdict(list)
    for e in graph["edges"]:
        if e["type"] == "EXECUTES":
            exec_edges[e["target"]].append(e)
        elif e["type"] == "EXPOSED_AS":
            exposed[e["source"]].append(e)
    for comp in list(results):
        for e in exec_edges.get(comp, []) + exposed.get(comp, []):
            other = e["source"] if e["type"] == "EXECUTES" else e["target"]
            if other in results or other in start_ids:
                continue
            base = results[comp]
            ev = e["evidence"][0] if e["evidence"] else {}
            results[other] = {
                "path": base["path"] + [other],
                "hops": base["hops"] + [{"from": comp, "to": other, "how": "runs" if e["type"] == "EXECUTES" else "exposed as API",
                                         "file": ev.get("file"), "line": ev.get("line")}],
                "impact_type": "structural" if e["type"] == "EXECUTES" else base["impact_type"],
            }

    impacted = []
    for comp, info in results.items():
        if comp in start_ids:
            continue
        distance = len(info["path"]) - 1
        node = graph_nodes.get(comp, {})
        ctype = _type(comp)
        impacted.append({
            "id": comp,
            "label": node.get("label", _label(comp)),
            "type": ctype,
            "file": node.get("file"),
            "line": node.get("line"),
            "distance": distance,
            "impact_type": info["impact_type"],
            "severity": _severity(distance, info["impact_type"], ctype),
            "path": [{"id": p, "label": graph_nodes.get(p, {}).get("label", _label(p)), "type": _type(p)} for p in info["path"]],
            "evidence": info["hops"],
        })
    order = {"High": 0, "Medium": 1, "Low": 2}
    impacted.sort(key=lambda x: (order[x["severity"]], x["distance"], x["type"], x["label"]))

    # Component-level impact graph (union of all paths)
    nodes, edges = {}, {}
    for s in start_ids:
        nodes[s] = {"id": s, "label": graph_nodes.get(s, {}).get("label", _label(s)), "type": _type(s), "is_start": True}
    for item in impacted:
        nodes[item["id"]] = {"id": item["id"], "label": item["label"], "type": item["type"], "severity": item["severity"]}
        for hop in item["evidence"]:
            key = (hop["from"], hop["to"])
            if key not in edges:
                edges[key] = {"source": hop["from"], "target": hop["to"], "how": hop["how"], "file": hop.get("file"),
                              "line": hop.get("line")}
            for end in key:
                nodes.setdefault(end, {"id": end, "label": graph_nodes.get(end, {}).get("label", _label(end)), "type": _type(end)})

    summary = defaultdict(int)
    for item in impacted:
        summary[item["type"]] += 1
    longest = max(impacted, key=lambda x: (x["type"] in {"api", "dataset"}, x["distance"]), default=None)
    return {
        "start": [{"id": s, "label": graph_nodes.get(s, {}).get("label", _label(s)), "type": _type(s)} for s in start_ids],
        "impacted": impacted,
        "summary": dict(summary),
        "severity_counts": {k: sum(1 for i in impacted if i["severity"] == k) for k in ("High", "Medium", "Low")},
        "graph": {"nodes": list(nodes.values()), "edges": list(edges.values())},
        "representative_chain": longest["path"] if longest else [],
    }


def _taint(starts: list[str], lineage: dict, results: dict, max_hops: int) -> None:
    adj = defaultdict(list)
    for e in lineage["edges"]:
        adj[e["source"]].append(e)

    parent: dict[str, tuple[str, dict] | None] = {s: None for s in starts}
    kind: dict[str, str] = {s: "data" for s in starts}
    queue = deque((s, False, 0) for s in starts)
    while queue:
        node, via_part_of, depth = queue.popleft()
        if depth >= max_hops:
            continue
        for e in adj.get(node, []):
            if via_part_of and e.get("how") == "record field":
                continue  # a field reached through its record must not taint the record's siblings
            nxt = e["target"]
            if nxt in parent:
                continue
            parent[nxt] = (node, e)
            kind[nxt] = "control" if (e["type"] == "CONTROL" or kind[node] == "control") else "data"
            queue.append((nxt, e["type"] == "PART_OF", depth + 1))

    # Collapse field-level paths to component-level paths
    for node in parent:
        comp = _component(node)
        if comp in results or _type(comp) not in COMPONENT_TYPES:
            continue
        chain = []
        cur = node
        while cur is not None:
            prev = parent[cur]
            chain.append((cur, prev[1] if prev else None))
            cur = prev[0] if prev else None
        chain.reverse()
        path, hops = [], []
        for lineage_node, edge in chain:
            c = _component(lineage_node)
            if not path:
                path.append(c)
                continue
            if c != path[-1]:
                ev = (edge or {}).get("evidence") or [{}]
                hops.append({"from": path[-1], "to": c, "how": (edge or {}).get("how") or (edge or {}).get("type", "").lower(),
                             "via": lineage_node, "file": ev[0].get("file"), "line": ev[0].get("line")})
                path.append(c)
        if len(path) != len(set(path)):
            continue
        results[comp] = {"path": path, "hops": hops, "impact_type": kind[node]}


def _structural(starts: list[str], graph: dict, results: dict, max_depth: int = 6) -> None:
    out_edges, in_edges = defaultdict(list), defaultdict(list)
    for e in graph["edges"]:
        out_edges[e["source"]].append(e)
        in_edges[e["target"]].append(e)

    def neighbours(node: str):
        t = _type(node)
        if t in {"program", "procedure", "external"}:
            for e in in_edges[node]:
                if e["type"] == "CALLS":
                    yield e["source"], e, "called by"
                elif e["type"] == "USES":
                    yield e["source"], e, "uses its type"
            for e in out_edges[node]:
                if e["type"] in {"WRITES", "EXPOSED_AS"} and not e["target"].startswith("column:"):
                    yield e["target"], e, "writes" if e["type"] == "WRITES" else "exposed as API"
        elif t in {"table", "dataset"}:
            for e in out_edges[node]:
                if e["type"] == "READ_BY":
                    yield e["target"], e, "read by"
            for e in in_edges[node]:
                if e["type"] == "WRITES" and node in starts:
                    yield e["source"], e, "written by"
                elif e["type"] == "MAPS_TO":
                    yield e["source"], e, "mapped by entity"
        elif t == "copybook":
            for e in out_edges[node]:
                if e["type"] == "INCLUDED_IN":
                    yield e["target"], e, "included in"
        elif t == "api":
            for e in in_edges[node]:
                if e["type"] == "EXPOSED_AS":
                    yield e["source"], e, "implemented by"

    queue = deque((s, [s], [], 0) for s in starts)
    seen = set(starts)
    while queue:
        node, path, hops, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for nxt, e, how in neighbours(node):
            if nxt in seen:
                continue
            seen.add(nxt)
            ev = e["evidence"][0] if e["evidence"] else {}
            new_hops = hops + [{"from": node, "to": nxt, "how": how, "file": ev.get("file"), "line": ev.get("line")}]
            if nxt not in results:
                results[nxt] = {"path": path + [nxt], "hops": new_hops, "impact_type": "structural"}
            queue.append((nxt, path + [nxt], new_hops, depth + 1))
