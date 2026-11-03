"""Read-only analysis results: overview, graph, rules, lineage, impact, documentation, modernization."""
import csv
import io
import json
import re
import zipfile
from collections import deque

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select

from app.deps import DB, CurrentUser, ReadyProject, WriterUser
from app.engine.business_view import build_file_view
from app.engine.graph_metrics import annotate_nodes, blast_radius
from app.engine.impact import analyze_impact
from app.engine.modernization import build_plan, parity_gherkin, parity_tests
from app.engine.overview_extras import data_hotspots, sensitive_data
from app.engine.plain import CATEGORY_MEANING, plain_rule
from app.models import BusinessRule, User
from app.llm import gateway
from app.llm.providers import LLMError
from app.schemas import FileImpactRequest, ImpactExplainRequest, ImpactRequest, ModernizationRequest, RuleReview
from app.services import ai_config, artifacts, audit, graph_store
from app.services.storage import project_dir

router = APIRouter(prefix="/projects/{project_id}", tags=["analysis"])


def _need(db, project_id: int, *kinds: str) -> dict:
    data = artifacts.load_many(db, project_id, list(kinds))
    missing = [k for k in kinds if k not in data]
    if missing:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Analysis results missing ({', '.join(missing)}); run the analysis again")
    return data


def _csv(rows: list[dict], filename: str) -> Response:
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


# --------------------------------------------------------------------------- overview & inventory

@router.get("/overview")
def overview(project: ReadyProject, db: DB) -> dict:
    d = _need(db, project.id, "insights")
    ins = d["insights"]
    programs = sorted(ins["programs"].values(), key=lambda s: -s["criticality_score"])
    dead = [p for p in programs if p.get("unused")]
    if "sensitive_data" not in ins:  # analysed before these existed: work them out from the saved results
        more = _need(db, project.id, "model", "graph")
        ins = dict(ins, sensitive_data=sensitive_data(more["model"], more["graph"]), data_hotspots=data_hotspots(more["graph"]))
    return {
        "sensitive_data": ins["sensitive_data"],
        "data_hotspots": ins["data_hotspots"],
        "changes": ins.get("changes"),
        "overview": ins["overview"],
        # What removing dead code saves: fewer programs and lines to migrate, test and run.
        "decommission": {"programs": len(dead), "lines": sum(p.get("loc", 0) for p in dead),
                         "share": round(sum(p.get("loc", 0) for p in dead) / max(1, ins["overview"].get("lines_of_code", 0)), 3),
                         "hubs": sum(1 for p in programs if p.get("criticality") == "Critical")},
        "capabilities": ins["capabilities"],
        "application_map": ins["application_map"],
        "programs": programs,
        "unused_components": ins["unused_components"],
        "external_interfaces": ins["external_interfaces"],
    }


@router.get("/api-surface")
def api_surface(project: ReadyProject, db: DB) -> dict:
    """Every API operation (documented and found in code), documentation drift, and the breaking and safe
    changes since the previous analysis."""
    data = artifacts.load(db, project.id, "api_surface")
    if data is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Run the analysis again to compute the API surface")
    return data


@router.get("/graph/search")
def graph_search(project: ReadyProject, db: DB, q: str = Query("", max_length=200),
                 types: str | None = Query(None, max_length=200), limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    """Find knowledge-graph components by type (program,table,job,...) and name, straight from the graph tables."""
    wanted = [t.strip() for t in types.split(",") if t.strip()] if types else None
    return graph_store.find_nodes(db, project.id, wanted, q.strip(), limit)


@router.get("/graph/neighbours")
def graph_neighbours(project: ReadyProject, db: DB, node: str = Query(..., max_length=512),
                     edge_types: str | None = Query(None, max_length=200)) -> dict:
    """Direct relationships of one component (callers, callees, tables read and written, ...) with evidence."""
    wanted = [t.strip() for t in edge_types.split(",") if t.strip()] if edge_types else None
    result = graph_store.neighbours(db, project.id, node, wanted)
    if not result["nodes"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Component not found in this application")
    return result


@router.get("/symbols")
def search_symbols(project: ReadyProject, db: DB, q: str = Query("", max_length=200),
                   types: str | None = None, limit: int = Query(20, le=100)) -> list[dict]:
    symbols = _need(db, project.id, "symbols")["symbols"]
    wanted = set(types.split(",")) if types else None
    nq = _norm(q)
    scored = []
    for s in symbols:
        if wanted and s["type"] not in wanted:
            continue
        name = _norm(s["name"])
        short = _norm(s["name"].split(".", 1)[-1])
        if not nq:
            rank = 3
        elif nq in (name, short):
            rank = 0
        elif name.startswith(nq) or short.startswith(nq):
            rank = 1
        elif nq in name:
            rank = 2
        else:
            continue
        scored.append((rank, len(s["name"]), s))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [s for _, _, s in scored[:limit]]


# --------------------------------------------------------------------------- dependency graph

@router.get("/graph")
def graph(project: ReadyProject, db: DB, types: str | None = None, include_columns: bool = False,
          include_fk: bool = False, focus: str | None = None, depth: int = Query(2, ge=1, le=6)) -> dict:
    d = _need(db, project.id, "graph", "insights")
    g = {"nodes": annotate_nodes(d["graph"]["nodes"], d["insights"]), "edges": d["graph"]["edges"]}
    wanted =set(types.split(",")) if types else {"job", "program", "table", "dataset", "api", "external", "procedure",
                                                  "copybook", "unresolved", "config"}
    if include_columns:
        wanted.add("column")
    hidden = {"HAS_COLUMN"} | (set() if include_fk else {"REFERENCES"})
    nodes = {n["id"]: n for n in g["nodes"] if n["type"] in wanted}
    edges = [e for e in g["edges"] if e["source"] in nodes and e["target"] in nodes and e["type"] not in hidden]
    if include_columns:
        edges += [e for e in g["edges"] if e["type"] == "HAS_COLUMN" and e["source"] in nodes and e["target"] in nodes]
    if focus:
        if focus not in nodes:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Focus node not found in the selected view")
        adj: dict[str, set] = {}
        for e in edges:
            adj.setdefault(e["source"], set()).add(e["target"])
            adj.setdefault(e["target"], set()).add(e["source"])
        keep, frontier = {focus}, deque([(focus, 0)])
        while frontier:
            node, d = frontier.popleft()
            if d >= depth:
                continue
            for nxt in adj.get(node, ()):
                if nxt not in keep:
                    keep.add(nxt)
                    frontier.append((nxt, d + 1))
        nodes = {k: v for k, v in nodes.items() if k in keep}
        edges = [e for e in edges if e["source"] in keep and e["target"] in keep]
    return {"nodes": list(nodes.values()), "edges": edges}


@router.get("/graph/node")
def node_detail(project: ReadyProject, db: DB, id: str = Query(..., max_length=512)) -> dict:
    d = _need(db, project.id, "graph", "insights", "model", "rules")
    node = next((n for n in d["graph"]["nodes"] if n["id"] == id), None)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Node not found")
    incoming = [e for e in d["graph"]["edges"] if e["target"] == id and e["type"] != "HAS_COLUMN"]
    outgoing = [e for e in d["graph"]["edges"] if e["source"] == id and e["type"] != "HAS_COLUMN"]
    detail: dict = {"node": annotate_nodes([node], d["insights"])[0], "incoming": incoming, "outgoing": outgoing,
                    "blast_radius": blast_radius(d["graph"], id)}
    if node["type"] == "program":
        name = node["label"]
        p = d["model"]["programs"].get(name)
        detail["stats"] = d["insights"]["programs"].get(name)
        if p:
            detail["paragraphs"] = p["paragraphs"]
            detail["parameters"] = p["using_params"]
            detail["rules"] = [r for r in d["rules"] if r["program"] == name and not r["is_technical"]]
    if node["type"] == "table":
        detail["columns"] = [e["target"] for e in d["graph"]["edges"] if e["source"] == id and e["type"] == "HAS_COLUMN"]
        t = d["model"]["tables"].get(node["label"])
        if t:
            detail["definition"] = t["columns"]
    return detail


# --------------------------------------------------------------------------- business rules

def _filter_rules(rules: list[dict], category: str | None, program: str | None, q: str | None,
                  include_technical: bool) -> list[dict]:
    out = []
    for r in rules:
        if r["is_technical"] and not include_technical:
            continue
        if category and r["category"] != category:
            continue
        if program and r["program"] != program:
            continue
        if q and q.lower() not in (r["description"] + " " + " ".join(r["fields"]) + " " + r["id"]).lower():
            continue
        out.append(r)
    return out


@router.get("/rules")
def rules(project: ReadyProject, db: DB, category: str | None = None, program: str | None = None,
          q: str | None = Query(None, max_length=200), include_technical: bool = False,
          status_filter: str | None = Query(None, alias="status", pattern="^(candidate|accepted|rejected)$")) -> dict:
    d = _need(db, project.id, "rules", "insights")
    all_rules = d["rules"]
    capability_of = {p: c["name"] for c in d["insights"].get("capabilities", []) for p in c["programs"]}
    reviews = {r.rule_id: r for r in db.scalars(select(BusinessRule).where(BusinessRule.project_id == project.id))}
    reviewer_ids = {r.reviewed_by for r in reviews.values() if r.reviewed_by}
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(reviewer_ids)))} if reviewer_ids else {}
    items = []
    for r in _filter_rules(all_rules, category, program, q, include_technical):
        review = reviews.get(r["id"])
        status_value = review.review_status if review else "candidate"
        if status_filter and status_value != status_filter:
            continue
        items.append({**r, "plain": plain_rule(r), "meaning": CATEGORY_MEANING.get(r["category"], ""),
                      "capability": capability_of.get(r["program"], "Uncategorised"),
                      "review_status": status_value, "review_note": review.review_note if review else "",
                      "review_reason": review.review_reason if review else "",
                      "reviewed_by": names.get(review.reviewed_by, "") if review else "",
                      "reviewed_at": review.reviewed_at if review else None})
    cats: dict[str, int] = {}
    for r in all_rules:
        if include_technical or not r["is_technical"]:
            cats[r["category"]] = cats.get(r["category"], 0) + 1
    counts = {"candidate": 0, "accepted": 0, "rejected": 0}
    for r in all_rules:
        if include_technical or not r["is_technical"]:
            review = reviews.get(r["id"])
            counts[review.review_status if review else "candidate"] += 1
    return {"items": items, "total": len(items), "categories": cats, "review_counts": counts,
            "programs": sorted({r["program"] for r in all_rules})}


@router.patch("/rules/{rule_id}/review")
def review_rule(rule_id: str, body: RuleReview, project: ReadyProject, user: WriterUser, db: DB,
                request: Request) -> dict:
    """Record a business reviewer's decision. It is kept when the code is analysed again."""
    row = db.scalar(select(BusinessRule).where(BusinessRule.project_id == project.id, BusinessRule.rule_id == rule_id))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found; run the analysis again")
    row.review_status, row.review_note = body.status, body.note.strip()
    row.review_reason = body.reason if body.status == "rejected" else ""
    row.reviewed_by, row.reviewed_at = user.id, datetime.now(timezone.utc)
    db.commit()
    audit.record(db, "rule_reviewed", user.id, f"project:{project.id}",
                 {"rule": rule_id, "status": body.status, "reason": row.review_reason, "note": row.review_note}, request)
    return {"id": rule_id, "review_status": row.review_status, "review_note": row.review_note,
            "review_reason": row.review_reason, "reviewed_by": user.full_name, "reviewed_at": row.reviewed_at}


@router.get("/graph/files")
def file_graph(project: ReadyProject, db: DB) -> dict:
    """File-level map with plain-language descriptions of every file and every link."""
    d = _need(db, project.id, "model", "graph", "insights", "rules")
    return build_file_view(d["model"], d["graph"], d["insights"], d["rules"])


@router.get("/rules/export")
def export_rules(project: ReadyProject, db: DB, include_technical: bool = False) -> Response:
    items = _filter_rules(_need(db, project.id, "rules")["rules"], None, None, None, include_technical)
    rows = [{"id": r["id"], "program": r["program"], "category": r["category"], "plain_english": plain_rule(r),
             "description": r["description"],
             "file": r["file"], "start_line": r["start_line"], "end_line": r["end_line"], "paragraph": r["paragraph"] or "",
             "fields": " ".join(r["fields"])} for r in items]
    return _csv(rows, f"business-rules-{project.id}.csv")


@router.get("/rules/spec")
def export_rule_spec(project: ReadyProject, db: DB) -> Response:
    """Business-confirmed rules as a JSON specification for the new system, each traced to its source lines.

    Only rules a person confirmed are included, so rejected plumbing and obsolete logic is not carried forward.
    """
    d = _need(db, project.id, "rules", "insights")
    capability_of = {p: c["name"] for c in d["insights"].get("capabilities", []) for p in c["programs"]}
    rows = {r.rule_id: r for r in db.scalars(select(BusinessRule).where(
        BusinessRule.project_id == project.id, BusinessRule.review_status == "accepted"))}
    names = {u.id: u.full_name for u in db.scalars(select(User).where(
        User.id.in_({r.reviewed_by for r in rows.values() if r.reviewed_by})))} if rows else {}
    spec = {
        "project": project.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rules": [{
            "id": r["id"], "capability": capability_of.get(r["program"], "Uncategorised"), "category": r["category"],
            "policy": plain_rule(r), "condition": r.get("condition") or "", "actions": r.get("actions") or [],
            "data": r["fields"],
            "source": {"program": r["program"], "file": r["file"], "lines": [r["start_line"], r["end_line"]],
                       "paragraph": r["paragraph"] or ""},
            "approval": {"by": names.get(rows[r["id"]].reviewed_by, ""), "note": rows[r["id"]].review_note,
                         "at": rows[r["id"]].reviewed_at.isoformat() if rows[r["id"]].reviewed_at else None},
        } for r in d["rules"] if r["id"] in rows],
    }
    return Response(json.dumps(spec, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="approved-rules-{project.id}.json"'})


# --------------------------------------------------------------------------- lineage

@router.get("/lineage")
def lineage(project: ReadyProject, db: DB, node: str = Query(..., max_length=512),
            depth: int = Query(4, ge=1, le=10), direction: str = Query("both", pattern="^(up|down|both)$")) -> dict:
    lin = _need(db, project.id, "lineage")["lineage"]
    nodes = {n["id"]: n for n in lin["nodes"]}
    if node not in nodes:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This element has no recorded data flow")
    out_adj, in_adj = {}, {}
    for e in lin["edges"]:
        out_adj.setdefault(e["source"], []).append(e)
        in_adj.setdefault(e["target"], []).append(e)
    keep, edges = {node: 0}, {}

    def walk(adj, key, sign):
        frontier = deque([(node, 0)])
        seen = {node}
        while frontier:
            cur, d = frontier.popleft()
            if d >= depth:
                continue
            for e in adj.get(cur, []):
                nxt = e[key]
                edges[(e["source"], e["target"], e["type"])] = e
                if nxt not in seen:
                    seen.add(nxt)
                    keep.setdefault(nxt, sign * (d + 1))
                    frontier.append((nxt, d + 1))

    if direction in ("down", "both"):
        walk(out_adj, "target", 1)
    if direction in ("up", "both"):
        walk(in_adj, "source", -1)
    return {"root": node, "nodes": [{**nodes[k], "level": v} for k, v in keep.items() if k in nodes],
            "edges": list(edges.values())}


@router.get("/lineage/examples")
def lineage_examples(project: ReadyProject, db: DB, limit: int = Query(5, le=20)) -> list[dict]:
    """Elements worth exploring first: the most connected columns, then the most critical programs."""
    d = _need(db, project.id, "lineage", "insights", "graph")
    degree: dict[str, int] = {}
    for e in d["lineage"]["edges"]:
        for end in (e["source"], e["target"]):
            if end.startswith("column:") and not re.search(r"_(ID|KEY|NO|NBR)$", end):
                degree[end] = degree.get(end, 0) + 1
    # Rank the best-connected candidates by how far a change to them actually reaches.
    candidates = sorted(degree, key=lambda k: -degree[k])[:15]
    reach = {c: len(analyze_impact([c], d["graph"], d["lineage"])["impacted"]) for c in candidates}
    cols = sorted(candidates, key=lambda c: (-reach[c], c))[:limit]
    programs = sorted(d["insights"]["programs"].values(), key=lambda s: -s["criticality_score"])[:2]
    return [{"id": c, "name": c.split(":", 1)[1], "type": "column"} for c in cols] + [
        {"id": f"program:{p['program']}", "name": p["program"], "type": "program"} for p in programs]


# --------------------------------------------------------------------------- impact

def _resolve_targets(text: str, graph: dict, lineage: dict) -> list[dict]:
    ids = {n["id"]: n for n in graph["nodes"]}
    lin = {n["id"]: n for n in lineage["nodes"]}
    if text in ids or text in lin:
        n = ids.get(text) or lin[text]
        return [{"id": text, "label": n["label"], "type": n["type"], "program": n.get("program")}]
    q = _norm(text)
    q_base = _norm(text.rsplit(".", 1)[0]) if re.search(r"\.(CBL|COB|CPY|JCL|SQL)$", text, re.I) else q
    exact, fields = [], []
    for n in graph["nodes"]:
        label = n["label"]
        if _norm(label) in (q, q_base) or (n["type"] == "column" and _norm(label.split(".", 1)[1]) == q):
            exact.append({"id": n["id"], "label": label, "type": n["type"]})
    for n in lineage["nodes"]:
        if n["type"] == "field" and _norm(n["label"]) == q:
            fields.append({"id": n["id"], "label": f"{n['program']}.{n['label']}", "type": "field", "program": n["program"]})
    return exact or fields


@router.post("/impact")
def impact(body: ImpactRequest, project: ReadyProject, db: DB, user: CurrentUser, request: Request) -> dict:
    d = _need(db, project.id, "graph", "lineage")
    target = body.target.strip()
    matches = _resolve_targets(target, d["graph"], d["lineage"])
    if not matches:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nothing named '{target}' was found in this application")
    if len(matches) > 1:
        return {"ambiguous": True, "options": matches}
    result = analyze_impact([matches[0]["id"]], d["graph"], d["lineage"])
    audit.record(db, "impact_analysis", user.id, f"project:{project.id}", {"target": matches[0]["id"]}, request)
    return {"ambiguous": False, **result}


FILE_START_TYPES = {"program", "copybook", "job", "procedure", "api", "table", "dataset", "external"}


def _norm_path(p: str | None) -> str:
    return (p or "").replace("\\", "/").lstrip("./").lower()


def _file_components(graph: dict) -> dict[str, list[dict]]:
    """Source file -> the analysed components defined in it (programs, classes, copybooks, jobs, tables...)."""
    out: dict[str, list[dict]] = {}
    for n in graph["nodes"]:
        if n.get("file") and n["type"] in FILE_START_TYPES:
            out.setdefault(_norm_path(n["file"]), []).append({"id": n["id"], "label": n["label"], "type": n["type"]})
    return out


def _file_starts(path: str, graph: dict) -> list[dict]:
    comps = _file_components(graph).get(_norm_path(path))
    if not comps:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "No programs, tables, jobs or other components were found in this file")
    return comps


@router.get("/impact/files")
def impact_files(project: ReadyProject, db: DB) -> dict:
    """Which source files contain something impact analysis can start from, for the file explorer."""
    comps = _file_components(_need(db, project.id, "graph")["graph"])
    return {"files": {p: [c["label"] for c in cs] for p, cs in comps.items()}}


@router.post("/impact/file")
def impact_for_file(body: FileImpactRequest, project: ReadyProject, db: DB, user: CurrentUser, request: Request) -> dict:
    """Impact of changing a whole file: every component defined in it is treated as changing."""
    d = _need(db, project.id, "graph", "lineage")
    starts = _file_starts(body.path, d["graph"])
    result = analyze_impact([c["id"] for c in starts], d["graph"], d["lineage"])
    audit.record(db, "impact_analysis", user.id, f"project:{project.id}", {"file": body.path}, request)
    return {"ambiguous": False, "file": body.path, **result}


IMPACT_SYSTEM = (
    "You explain the impact of a change in a legacy application to business and delivery leads. "
    "Use only the facts given; do not invent components. Plain English, no code. "
    'Return a JSON object: {"summary": "2-4 sentences", "business_effect": ["..."], '
    '"risks": ["..."], "test_first": ["..."]}. Keep each list to at most 5 short items.'
)


def _impact_prompt(what: str, result: dict) -> str:
    sev = result.get("severity_counts", {})
    lines = [f"Change: {what}",
             f"Impacted components: {len(result['impacted'])} (High {sev.get('High', 0)}, "
             f"Medium {sev.get('Medium', 0)}, Low {sev.get('Low', 0)})",
             "Top impacted (severity | type | name | how it is reached):"]
    for i in result["impacted"][:25]:
        how = ", ".join(dict.fromkeys(h["how"] for h in i["evidence"] if h.get("how")))
        why = {"data": "data flows into it", "control": "a decision depends on it"}.get(i["impact_type"], "structural dependency")
        lines.append(f"- {i['severity']} | {i['type']} | {i['label']} | {' -> '.join(p['label'] for p in i['path'])} "
                     f"({why}{'; ' + how if how else ''})")
    if len(result["impacted"]) > 25:
        lines.append(f"... and {len(result['impacted']) - 25} more, lower severity or further away.")
    return "\n".join(lines)


@router.post("/impact/explain")
def explain_impact(body: ImpactExplainRequest, project: ReadyProject, db: DB, user: CurrentUser, request: Request) -> dict:
    """Optional AI summary of an impact result. The impact itself stays deterministic; only the wording is AI."""
    if bool(body.target) == bool(body.path):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give either a target or a file path")
    llm = ai_config.to_llm_config(ai_config.get_row(db))
    if not llm.configured:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No AI model is set up. An administrator can add one in Settings → AI models.")
    d = _need(db, project.id, "graph", "lineage")
    if body.path:
        starts = _file_starts(body.path, d["graph"])
        what = f"file {body.path} (contains {', '.join(c['label'] for c in starts)})"
    else:
        matches = _resolve_targets(body.target.strip(), d["graph"], d["lineage"])
        if len(matches) != 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Run the impact analysis and pick one element first")
        starts, what = matches, f"{matches[0]['type']} {matches[0]['label']}"
    result = analyze_impact([c["id"] for c in starts], d["graph"], d["lineage"])
    if not result["impacted"]:
        return {"summary": "Nothing else in the application depends on this, so the change is contained.",
                "business_effect": [], "risks": [], "test_first": [], "model": None, "cached": False}
    try:
        res = gateway.complete(db, llm, IMPACT_SYSTEM, _impact_prompt(what, result), purpose="impact_explain",
                               tier="small", project_id=project.id, user_id=user.id,
                               sources=[{"node": c["id"]} for c in starts])
    except LLMError as exc:
        db.commit()  # keep the usage log row
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"The AI model call failed: {exc}")
    db.commit()
    from app.engine.qa import parse_json

    parsed = parse_json(res.text) or {"summary": res.text.strip()}

    def _list(key: str) -> list[str]:
        v = parsed.get(key)
        return [str(x).strip() for x in v if str(x).strip()][:5] if isinstance(v, list) else []

    audit.record(db, "impact_explained", user.id, f"project:{project.id}", {"what": what, "model": res.model}, request)
    return {"summary": str(parsed.get("summary", "")).strip(), "business_effect": _list("business_effect"),
            "risks": _list("risks"), "test_first": _list("test_first"), "model": res.model, "cached": res.cached}


@router.get("/impact/export")
def export_impact(project: ReadyProject, db: DB, target: str | None = Query(None, max_length=512),
                  path: str | None = Query(None, max_length=1024)) -> Response:
    d = _need(db, project.id, "graph", "lineage")
    if path:
        result = analyze_impact([c["id"] for c in _file_starts(path, d["graph"])], d["graph"], d["lineage"])
    elif target:
        result = analyze_impact([target], d["graph"], d["lineage"])
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give a target or a file path")
    rows = [{"component": i["label"], "type": i["type"], "severity": i["severity"], "impact_type": i["impact_type"],
             "distance": i["distance"], "path": " -> ".join(p["label"] for p in i["path"]),
             "evidence": "; ".join(f"{h['file']}:{h['line']}" for h in i["evidence"] if h.get("file"))} for i in result["impacted"]]
    return _csv(rows, f"impact-{project.id}.csv")


# --------------------------------------------------------------------------- documentation

@router.get("/docs")
def list_docs(project: ReadyProject, db: DB) -> list[dict]:
    docs = _need(db, project.id, "docs")["docs"]
    return [{"id": x["id"], "title": x["title"], "category": x["category"]} for x in docs]


@router.get("/docs/export")
def export_docs(project: ReadyProject, db: DB) -> StreamingResponse:
    docs = _need(db, project.id, "docs")["docs"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc in docs:
            zf.writestr(f"{doc['id']}.md", doc["markdown"])
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="documentation-{project.id}.zip"'})


@router.get("/docs/{doc_id}")
def get_doc(doc_id: str, project: ReadyProject, db: DB) -> dict:
    docs = _need(db, project.id, "docs")["docs"]
    doc = next((x for x in docs if x["id"] == doc_id), None)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


# --------------------------------------------------------------------------- modernization

@router.post("/modernization")
def modernization(body: ModernizationRequest, project: ReadyProject, db: DB) -> dict:
    d = _need(db, project.id, "model", "graph", "rules", "insights")
    return build_plan(project_dir(project.id), d["model"], d["graph"], d["rules"], d["insights"],
                      body.language, body.database, body.platform)


@router.get("/modernization/parity")
def parity(project: ReadyProject, db: DB, format: str = Query("csv", pattern="^(csv|gherkin|json)$")):
    tests = parity_tests(_need(db, project.id, "rules")["rules"])
    if format == "json":
        return tests
    if format == "gherkin":
        return Response(parity_gherkin(tests), media_type="text/plain",
                        headers={"Content-Disposition": f'attachment; filename="parity-{project.id}.feature"'})
    return _csv(tests, f"parity-tests-{project.id}.csv")
