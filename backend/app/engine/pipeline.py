"""End-to-end analysis pipeline: inventory -> parse -> knowledge graph -> lineage -> rules -> insights -> docs."""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from app.engine.api_surface import build_surface, drift
from app.engine.docs import generate_docs
from app.engine.insights import compute_insights
from app.engine.knowledge import build_component_graph, build_lineage
from app.engine.model import build_model, inventory
from app.engine.overview_extras import data_hotspots, sensitive_data
from app.engine.parse_cache import FileParseCache
from app.engine.retrieval import build_chunks, build_symbols
from app.engine.rules import extract_rules

ProgressFn = Callable[[int, str], None]


def run_analysis(root: Path, project_name: str, progress: ProgressFn | None = None,
                 cache: FileParseCache | None = None) -> dict:
    """Return every analysis artifact for the sources under root, keyed by artifact kind.

    Pass a parse cache to skip re-parsing files whose content has not changed since the last run.
    """
    report = progress or (lambda pct, stage: None)
    started = time.perf_counter()

    report(5, "Building file inventory")
    files = inventory(root)
    if not files:
        raise ValueError("No supported source files found. Upload COBOL, copybooks, JCL, SQL, Java, C#, Python, "
                         "JavaScript/TypeScript, shell scripts, YAML config, OpenAPI or docs.")

    report(15, f"Parsing {len(files)} source files")
    model = build_model(root, files, cache)
    if cache is not None:
        cache.prune()

    report(40, "Building the Legacy Knowledge Graph")
    graph = build_component_graph(model)

    report(55, "Tracing field-level data lineage")
    lineage = build_lineage(model, graph)

    report(65, "Extracting business rules")
    rules = extract_rules(model, lineage)

    report(75, "Scoring criticality and finding unused components")
    insights = compute_insights(model, graph, rules)
    insights["sensitive_data"] = sensitive_data(model, graph)
    insights["data_hotspots"] = data_hotspots(graph)

    report(85, "Generating documentation")
    docs = generate_docs(model, graph, lineage, rules, insights, project_name)

    report(92, "Indexing code for question answering")
    chunks = build_chunks(root, model)
    symbols = build_symbols(model, graph, lineage)

    surface = build_surface(model)
    api_surface = {"operations": surface, "drift": drift(surface)}
    insights["overview"]["api_operations"] = len(surface)
    insights["overview"]["undocumented_endpoints"] = len(api_surface["drift"]["undocumented"])

    insights["overview"]["analysis_seconds"] = round(time.perf_counter() - started, 2)
    insights["overview"]["files"] = len(files)
    insights["overview"]["languages"] = _languages(files)
    if cache is not None:
        insights["overview"].update(cache.stats())
    return {
        "inventory": files,
        "model": model,
        "graph": graph,
        "lineage": lineage,
        "rules": rules,
        "insights": insights,
        "docs": docs,
        "chunks": chunks,
        "symbols": symbols,
        "api_surface": api_surface,
    }


def _languages(files: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in files:
        out[f["language"]] = out.get(f["language"], 0) + 1
    return out
