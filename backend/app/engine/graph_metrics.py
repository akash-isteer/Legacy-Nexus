"""Graph overlays for the dependency map: hub / dead-code flags per node and the blast radius of a change.

Everything here is computed from the stored graph and insights, so it costs no AI tokens.
"""
from __future__ import annotations

from collections import deque

# For each edge type: does a change to the edge's source affect its target ("forward"), or the other way round?
# CALLS a->b: a depends on b, so a change to b affects a.
_AFFECTS = {
    "CALLS": "backward", "USES": "backward", "MAPS_TO": "backward",
    "EXECUTES": "backward",          # job -> program: changing the program affects the job
    "EXPOSED_AS": "forward",         # program -> api
    "WRITES": "forward",             # program -> table/dataset: readers of that data are affected
    "READ_BY": "forward",            # table -> program
    "INCLUDED_IN": "forward",        # copybook -> program
}
_COUNTED = ("program", "job", "api", "table", "dataset", "procedure", "external")


def annotate_nodes(nodes: list[dict], insights: dict) -> list[dict]:
    """Copy of the nodes with criticality, fan-in/out, hub and unused flags for programs, tables and copybooks."""
    programs = insights.get("programs", {})
    unused = {(u["type"], u["name"]) for u in insights.get("unused_components", [])}
    out = []
    for n in nodes:
        n = dict(n)
        if n["type"] == "program" and n["label"] in programs:
            s = programs[n["label"]]
            n.update({
                "criticality": s.get("criticality", ""), "score": s.get("criticality_score", 0),
                "fan_in": s.get("fan_in", 0), "fan_out": s.get("fan_out", 0),
                "unused": bool(s.get("unused")), "hub": s.get("criticality") == "Critical",
            })
        elif (n["type"], n["label"]) in unused:
            n["unused"] = True
        out.append(n)
    return out


def blast_radius(graph: dict, node_id: str, sample: int = 12) -> dict:
    """Everything that may need re-testing if `node_id` changes, found by walking dependencies outwards."""
    affects: dict[str, set[str]] = {}
    for e in graph["edges"]:
        way = _AFFECTS.get(e["type"])
        if way == "forward":
            affects.setdefault(e["source"], set()).add(e["target"])
        elif way == "backward":
            affects.setdefault(e["target"], set()).add(e["source"])
    seen: dict[str, int] = {node_id: 0}
    queue = deque([node_id])
    while queue:
        cur = queue.popleft()
        for nxt in affects.get(cur, ()):
            if nxt not in seen:
                seen[nxt] = seen[cur] + 1
                queue.append(nxt)
    seen.pop(node_id)
    counts = {t: 0 for t in _COUNTED}
    for nid in seen:
        t = nid.split(":", 1)[0]
        if t in counts:
            counts[t] += 1
    nearest = sorted((d, nid) for nid, d in seen.items() if nid.split(":", 1)[0] in counts)
    return {
        "total": sum(counts.values()),
        "counts": counts,
        "max_depth": max(seen.values(), default=0),
        "sample": [{"id": nid, "hops": d} for d, nid in nearest[:sample]],
    }
