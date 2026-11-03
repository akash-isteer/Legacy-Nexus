"""Engine tests on the bundled sample application, covering the five problem statements."""
from pathlib import Path

import pytest

from app.engine.impact import analyze_impact
from app.engine.pipeline import run_analysis
from app.engine.qa import NOT_FOUND, QAEngine, QASettings
from app.parsers.cobol import parse_cobol

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "banking-core"


@pytest.fixture(scope="module")
def analysis():
    return run_analysis(SAMPLE, "Core Banking")


def _edge(graph, source, etype, target):
    return any(e["source"] == source and e["type"] == etype and e["target"] == target for e in graph["edges"])


def test_problem2_dependency_chain(analysis):
    g = analysis["graph"]
    assert _edge(g, "job:NIGHTLY", "EXECUTES", "program:PAYMENT")
    assert _edge(g, "program:PAYMENT", "CALLS", "program:ACCOUNT")
    assert _edge(g, "program:PAYMENT", "WRITES", "table:PAYMENT_LEDGER")
    assert _edge(g, "table:PAYMENT_LEDGER", "READ_BY", "program:REPORT12")
    assert _edge(g, "program:REPORT12", "WRITES", "dataset:BANK.REPORTS.REPORT12")
    assert _edge(g, "job:MONTHEND", "EXECUTES", "procedure:SP_CLOSE_DORMANT")
    # every edge carries evidence
    assert all(e["evidence"] for e in g["edges"] if e["type"] != "HAS_COLUMN")


def test_problem1_interest_facts(analysis):
    g = analysis["graph"]
    assert _edge(g, "program:ACCOUNT", "CALLS", "program:INTEREST")
    assert _edge(g, "column:ACCOUNT_MASTER.INTEREST_RATE", "READ_BY", "program:INTEREST")


def test_problem3_documentation_sets(analysis):
    ids = {d["id"] for d in analysis["docs"]}
    assert ids == {"application-overview", "module-documentation", "api-documentation", "data-flow-documentation",
                   "business-rule-documentation", "database-mapping", "dependency-documentation"}


def test_problem4_rules_are_traceable(analysis):
    rules = [r for r in analysis["rules"] if not r["is_technical"]]
    assert len(rules) >= 15
    min_balance = next(r for r in rules if "MIN-BALANCE" in r["description"])
    assert min_balance["program"] == "ACCOUNT" and min_balance["file"].endswith("ACCOUNT.CBL")
    assert "500.00" in min_balance["description"]
    for r in rules:
        assert r["start_line"] <= r["end_line"]


def test_problem5_customer_address_impact(analysis):
    result = analyze_impact(["column:CUSTOMER_MASTER.CUSTOMER_ADDRESS"], analysis["graph"], analysis["lineage"])
    impacted = {i["id"]: i for i in result["impacted"]}
    for expected in ("program:CUSTOMER", "program:ACCOUNT", "program:PAYMENT", "program:REPORT12",
                     "api:API_CUSTOMER", "dataset:BANK.REPORTS.REPORT12"):
        assert expected in impacted, expected
    assert impacted["program:CUSTOMER"]["severity"] == "High"
    assert "program:LEGACYTX" not in impacted


def test_unused_components(analysis):
    names = {u["name"] for u in analysis["insights"]["unused_components"]}
    assert {"LEGACYTX", "BRANCH_LEGACY", "AUDIT_ARCHIVE"} <= names


def test_qa_abstains_without_evidence(analysis):
    eng = QAEngine(analysis["chunks"], analysis["symbols"], analysis["graph"], analysis["lineage"],
                   {f["path"]: f for f in analysis["inventory"]})
    r = eng.answer("What is the capital of France?", QASettings(), None)
    assert r["not_found"] and r["answer"] == NOT_FOUND


def test_qa_retrieval_never_sends_whole_files(analysis):
    eng = QAEngine(analysis["chunks"], analysis["symbols"], analysis["graph"], analysis["lineage"],
                   {f["path"]: f for f in analysis["inventory"]})
    r = eng.retrieve("Where is interest calculated?", QASettings(top_k=4))
    assert r.mode == "keyword" and r.evidence[0]["file"] == "cobol/INTEREST.CBL"
    assert all(c["end_line"] - c["start_line"] < 60 for c in r.evidence)
    assert any("INTEREST" in f["text"] for f in r.facts)


def test_qa_rejects_fabricated_citations(analysis):
    eng = QAEngine(analysis["chunks"], analysis["symbols"], analysis["graph"], analysis["lineage"],
                   {f["path"]: f for f in analysis["inventory"]})
    r = eng.retrieve("Where is interest calculated?", QASettings())
    evidence, facts = r.evidence, r.facts
    v = eng.validate({"answer": "x", "claims": [
        {"text": "real", "citations": [{"file": "INTEREST.CBL", "line_start": 63, "line_end": 64}]},
        {"text": "fake", "citations": [{"file": "TAXCALC.CBL", "line_start": 1, "line_end": 2}]},
        {"text": "bad lines", "citations": [{"file": "cobol/INTEREST.CBL", "line_start": 900, "line_end": 901}]},
    ]}, evidence, facts)
    assert v["citations_valid"] == 1 and v["citations_total"] == 3


def test_free_format_cobol_is_supported():
    src = "IDENTIFICATION DIVISION.\nPROGRAM-ID. FREEPGM.\nPROCEDURE DIVISION.\nMAIN-PARA.\n    CALL 'OTHER'\n    STOP RUN.\n"
    p = parse_cobol("FREE.cbl", src)
    assert p.program_id == "FREEPGM"
    assert [c["target"] for c in p.calls] == ["OTHER"]
