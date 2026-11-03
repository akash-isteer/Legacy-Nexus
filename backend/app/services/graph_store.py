"""Writes the knowledge graph and business rules into relational tables, and queries them.

The JSON artifacts remain the source for the existing screens; these tables make the same facts queryable
(by type, name, neighbour) and keep human review decisions on rules across re-analyses.
"""
from __future__ import annotations

import hashlib

from sqlalchemy import delete, insert, or_, select
from sqlalchemy.orm import Session

from app.models import BusinessRule, GraphEdge, GraphNode

_NODE_COLUMNS = {"id", "type", "label", "file", "line"}
_EDGE_COLUMNS = {"source", "target", "type", "evidence"}


def rule_fingerprint(rule: dict) -> str:
    """Stable identity of a rule: where it lives and what it tests, not its BR- number or wording."""
    parts = [rule.get("program") or "", rule.get("kind") or "", rule.get("paragraph") or "",
             rule.get("condition") or "", "|".join(rule.get("actions") or []), ",".join(rule.get("fields") or [])]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def save(db: Session, project_id: int, graph: dict, rules: list[dict]) -> None:
    """Replace the project's graph and rule rows. Review decisions carry over by fingerprint."""
    reviews = {
        r.fingerprint: r for r in db.scalars(
            select(BusinessRule).where(BusinessRule.project_id == project_id,
                                       BusinessRule.review_status != "candidate"))
    }
    kept_reviews = {fp: (r.review_status, r.review_note, r.review_reason, r.reviewed_by, r.reviewed_at)
                    for fp, r in reviews.items()}

    db.execute(delete(GraphEdge).where(GraphEdge.project_id == project_id))
    db.execute(delete(GraphNode).where(GraphNode.project_id == project_id))
    db.execute(delete(BusinessRule).where(BusinessRule.project_id == project_id))

    node_rows = [{
        "project_id": project_id, "node_id": n["id"], "type": n["type"], "label": str(n.get("label", ""))[:512],
        "file": n.get("file"), "line": n.get("line") if isinstance(n.get("line"), int) else None,
        "meta": {k: v for k, v in n.items() if k not in _NODE_COLUMNS},
    } for n in graph["nodes"]]
    edge_rows = [{
        "project_id": project_id, "source": e["source"], "target": e["target"], "type": e["type"],
        "evidence": e.get("evidence") or [], "meta": {k: v for k, v in e.items() if k not in _EDGE_COLUMNS},
    } for e in graph["edges"]]
    rule_rows = []
    for r in rules:
        fp = rule_fingerprint(r)
        status, note, reason, by, at = kept_reviews.get(fp, ("candidate", "", "", None, None))
        rule_rows.append({
            "project_id": project_id, "rule_id": r["id"], "fingerprint": fp, "program": r["program"],
            "file": r["file"], "start_line": r["start_line"], "end_line": r["end_line"], "category": r["category"],
            "is_technical": r["is_technical"], "description": r["description"], "data": r,
            "review_status": status, "review_note": note, "review_reason": reason, "reviewed_by": by, "reviewed_at": at,
        })
    for model, rows in ((GraphNode, node_rows), (GraphEdge, edge_rows), (BusinessRule, rule_rows)):
        for i in range(0, len(rows), 1000):  # bounded batches keep memory and statement size predictable
            db.execute(insert(model), rows[i:i + 1000])


def find_nodes(db: Session, project_id: int, types: list[str] | None = None, q: str = "",
               limit: int = 50) -> list[dict]:
    stmt = select(GraphNode).where(GraphNode.project_id == project_id)
    if types:
        stmt = stmt.where(GraphNode.type.in_(types))
    if q:
        stmt = stmt.where(GraphNode.label.ilike(f"%{q}%"))
    stmt = stmt.order_by(GraphNode.type, GraphNode.label).limit(limit)
    return [_node_dict(n) for n in db.scalars(stmt)]


def neighbours(db: Session, project_id: int, node_id: str, edge_types: list[str] | None = None) -> dict:
    stmt = select(GraphEdge).where(GraphEdge.project_id == project_id,
                                   or_(GraphEdge.source == node_id, GraphEdge.target == node_id))
    if edge_types:
        stmt = stmt.where(GraphEdge.type.in_(edge_types))
    edges = list(db.scalars(stmt))
    ids = {node_id} | {e.source for e in edges} | {e.target for e in edges}
    nodes = db.scalars(select(GraphNode).where(GraphNode.project_id == project_id, GraphNode.node_id.in_(ids)))
    return {
        "node": node_id,
        "nodes": [_node_dict(n) for n in nodes],
        "edges": [{"source": e.source, "target": e.target, "type": e.type, "evidence": e.evidence,
                   "direction": "out" if e.source == node_id else "in"} for e in edges],
    }


def _node_dict(n: GraphNode) -> dict:
    return {"id": n.node_id, "type": n.type, "label": n.label, "file": n.file, "line": n.line, **(n.meta or {})}
