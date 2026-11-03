"""Multi-language analysis: Java, C#, Python, JavaScript/TypeScript, shell, YAML config, call resolution
with UNRESOLVED_CALL, and API drift / breaking-change detection."""
import io
import shutil
import zipfile
from pathlib import Path

import pytest

from app.engine.api_surface import build_surface, diff_surfaces
from app.engine.model import build_model, inventory
from app.engine.pipeline import run_analysis

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "polyglot"


@pytest.fixture(scope="module")
def analysis():
    return run_analysis(FIXTURE, "Digital Banking")


def edge(a, source, etype, target):
    return any(e["source"] == source and e["type"] == etype and e["target"] == target for e in a["graph"]["edges"])


def unresolved(a, source):
    return {e["target"]: e.get("candidates") for e in a["graph"]["edges"]
            if e["type"] == "UNRESOLVED_CALL" and e["source"] == source}


def test_all_languages_are_recognised(analysis):
    langs = analysis["insights"]["overview"]["languages"]
    assert {"java", "csharp", "python", "typescript", "javascript", "shell", "config", "api"} <= set(langs)
    assert analysis["insights"]["overview"]["warnings"] == []


def test_dependency_folders_are_ignored(tmp_path):
    root = tmp_path / "src"
    shutil.copytree(FIXTURE, root)
    vendored = root / "node" / "node_modules" / "left-pad" / "index.js"
    vendored.parent.mkdir(parents=True)
    vendored.write_text("module.exports = function leftPad() {};\n")
    (root / "node" / "src" / "app.min.js").write_text("function a(){}")
    paths = {f["path"] for f in inventory(root)}
    assert not any("node_modules" in p or p.endswith(".min.js") for p in paths)


# ------------------------------------------------------------------ Java


def test_java_calls_through_interface_reach_the_implementation(analysis):
    assert edge(analysis, "program:AccountController", "CALLS", "program:AccountServiceImpl")
    assert edge(analysis, "program:AccountController", "USES", "program:AccountService")


def test_java_orm_and_jdbc_table_access(analysis):
    assert edge(analysis, "table:ACCOUNT_MASTER", "READ_BY", "program:AccountServiceImpl")  # JDBC + findById
    assert edge(analysis, "program:AccountServiceImpl", "WRITES", "table:ACCOUNT_MASTER")   # accounts.save()
    assert edge(analysis, "program:AccountServiceImpl", "WRITES", "table:PAYMENT_LEDGER")   # INSERT string
    assert edge(analysis, "program:Account", "MAPS_TO", "table:ACCOUNT_MASTER")             # @Entity @Table


def test_runtime_dispatch_is_flagged_not_guessed(analysis):
    calls = unresolved(analysis, "program:AccountServiceImpl")
    assert calls == {"unresolved:handler.handle": ["CardHandler", "WireHandler"]}
    assert not edge(analysis, "program:AccountServiceImpl", "CALLS", "program:CardHandler")
    unused = {u["name"] for u in analysis["insights"]["unused_components"]}
    assert not {"CardHandler", "WireHandler"} & unused  # possibly called, so never reported as unused


def test_java_endpoints_and_rules(analysis):
    api = analysis["model"]["apis"]["AccountController_API"]
    ops = {(o["method"], o["path"]) for o in api["operations"]}
    assert ops == {("GET", "/api/accounts/{accountId}"), ("POST", "/api/accounts/{accountId}/payments")}
    assert not api["documented"]
    rules = [r["description"] for r in analysis["rules"] if r["program"] == "AccountServiceImpl"]
    assert any("MAX_PAYMENT (250000.00)" in d and "hold for review" in d for d in rules)
    assert any("MIN_BALANCE (500)" in d for d in rules)


# ------------------------------------------------------------------ C#


def test_csharp_controller_service_and_entity_framework(analysis):
    assert edge(analysis, "program:CustomerController", "CALLS", "program:CustomerService")
    assert edge(analysis, "table:CUSTOMER_MASTER", "READ_BY", "program:CustomerService")      # _db.Customers.Where
    assert edge(analysis, "program:CustomerService", "WRITES", "column:CUSTOMER_MASTER.CUSTOMER_ADDRESS")  # raw SQL
    assert edge(analysis, "program:Customer", "MAPS_TO", "table:CUSTOMER_MASTER")
    validation = [r for r in analysis["rules"] if r["program"] == "CustomerService" and r["category"] == "Validation"]
    assert validation and "MaxAddressLength (80)" in validation[0]["description"]


def test_documented_api_is_linked_to_its_implementation(analysis):
    assert edge(analysis, "program:CustomerController", "EXPOSED_AS", "api:API_BRANCH_CUSTOMER")


# ------------------------------------------------------------------ Python, JS/TS


def test_python_routes_orm_and_dynamic_calls(analysis):
    assert edge(analysis, "program:statements.api", "CALLS", "program:StatementService")
    assert edge(analysis, "program:StatementService", "WRITES", "table:STATEMENTS")  # session.add(Statement(...))
    assert edge(analysis, "table:PAYMENT_LEDGER", "READ_BY", "program:StatementService")
    assert unresolved(analysis, "program:StatementService") == {
        "unresolved:exporter.render": ["CsvExporter", "PdfExporter"]}
    assert analysis["model"]["programs"]["statements.load_statements"]["entry_point"]
    assert any("OVERDRAFT_LIMIT (1000)" in r["description"] for r in analysis["rules"] if r["program"] == "StatementService")


def test_typescript_and_javascript(analysis):
    assert edge(analysis, "program:routes/notifications", "CALLS", "program:Notifier")
    assert edge(analysis, "program:routes/notifications", "EXPOSED_AS", "api:routes/notifications_API")
    assert edge(analysis, "table:CUSTOMER_MASTER", "READ_BY", "program:Notifier")
    assert edge(analysis, "program:jobs/notify", "CALLS", "program:Notifier")
    assert analysis["model"]["programs"]["jobs/notify"]["entry_point"]


def test_library_calls_are_not_mistaken_for_project_calls(tmp_path):
    (tmp_path / "client.py").write_text(
        "import requests\n\nclass Cache:\n    def get(self, key):\n        return key\n\n"
        "def fetch(url):\n    return requests.get(url)\n")
    model = build_model(tmp_path, inventory(tmp_path))
    assert model["programs"]["client"]["calls"] == []  # requests.get is a library call, not Cache.get


# ------------------------------------------------------------------ shell, config, COBOL


def test_shell_script_is_a_job_running_the_programs(analysis):
    for target in ("program:statements.load_statements", "program:jobs/notify", "program:ReconcileJob"):
        assert edge(analysis, "job:nightly_digital", "EXECUTES", target), target
    assert edge(analysis, "program:statements.load_statements", "WRITES", "dataset:/var/log/bank/statements.log")
    assert edge(analysis, "dataset:/data/in/ledger_extract.csv", "READ_BY", "program:ReconcileJob")
    assert set(unresolved(analysis, "job:nightly_digital")) == {"unresolved:./scripts/cleanup.sh"}


def test_config_names_the_external_systems(analysis):
    externals = {e["target"] for e in analysis["graph"]["edges"] if e["type"] == "CONFIGURES"}
    assert externals == {"external:mainframe.bank.local:50000", "external:kafka.bank.local:9092",
                         "external:audit.bank.local", "external:payments-posted"}


def test_cobol_dynamic_call_is_unresolved(tmp_path):
    (tmp_path / "ROUTER.cbl").write_text(
        "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. ROUTER.\n       DATA DIVISION.\n"
        "       WORKING-STORAGE SECTION.\n       01  WS-PGM  PIC X(8).\n       PROCEDURE DIVISION.\n"
        "       MAIN-PARA.\n           CALL WS-PGM\n           STOP RUN.\n")
    a = run_analysis(tmp_path, "Router")
    assert unresolved(a, "program:ROUTER") == {"unresolved:WS-PGM": []}
    assert a["insights"]["overview"]["unresolved_calls"] == 1


# ------------------------------------------------------------------ API drift and breaking changes


def test_documentation_drift(analysis):
    d = analysis["api_surface"]["drift"]
    undocumented = {(o["method"], o["path"]) for o in d["undocumented"]}
    assert ("PUT", "/api/customers/{id}/address") in undocumented
    assert {(o["method"], o["path"]) for o in d["not_implemented"]} == {("DELETE", "/api/customers/{id}")}
    assert "GET /api/customers/{}" in d["matched"]


def _break_the_api(root: Path) -> None:
    ctrl = root / "java" / "com" / "bank" / "digital" / "AccountController.java"
    src = ctrl.read_text()
    src = src.replace("public AccountDto getAccount(@PathVariable String accountId)",
                      "public AccountDto getAccount(@PathVariable String accountId, @RequestParam String channel)")
    start = src.index("    @PostMapping")
    end = src.index("    }\n", start) + len("    }\n")
    ctrl.write_text(src[:start] + src[end:])


def test_breaking_changes_between_versions(tmp_path):
    root = tmp_path / "src"
    shutil.copytree(FIXTURE, root)
    before = build_surface(build_model(root, inventory(root)))
    _break_the_api(root)
    after = build_surface(build_model(root, inventory(root)))
    changes = diff_surfaces(before, after)
    kinds = {(c["kind"], c["method"], c["path"]) for c in changes["breaking"]}
    assert ("removed", "POST", "/api/accounts/{accountId}/payments") in kinds
    assert ("required_parameter_added", "GET", "/api/accounts/{accountId}") in kinds
    assert diff_surfaces(after, after)["summary"] == {"breaking": 0, "non_breaking": 0}


def _zip(root: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in root.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(root).as_posix())
    return buf.getvalue()


def test_api_reports_breaking_changes_after_reanalysis(client, auth, tmp_path):
    pid = client.post("/api/projects", json={"name": "Digital banking"}, headers=auth).json()["id"]
    files = [("files", ("digital.zip", _zip(FIXTURE), "application/zip"))]
    assert client.post(f"/api/projects/{pid}/files", files=files, headers=auth).status_code == 200
    assert client.post(f"/api/projects/{pid}/analyze", headers=auth).status_code == 202
    first = client.get(f"/api/projects/{pid}/api-surface", headers=auth).json()
    assert first["changes"]["baseline"] and first["drift"]["not_implemented"]

    root = tmp_path / "v2"
    shutil.copytree(FIXTURE, root)
    _break_the_api(root)
    changed = root / "java" / "com" / "bank" / "digital" / "AccountController.java"
    upload = [("files", ("java/com/bank/digital/AccountController.java", changed.read_bytes(), "text/plain"))]
    assert client.post(f"/api/projects/{pid}/files", files=upload, headers=auth).status_code == 200
    assert client.post(f"/api/projects/{pid}/analyze", headers=auth).status_code == 202
    second = client.get(f"/api/projects/{pid}/api-surface", headers=auth).json()
    assert second["changes"]["summary"]["breaking"] == 2
    overview = client.get(f"/api/projects/{pid}/overview", headers=auth).json()["overview"]
    assert overview["api_breaking_changes"] == 2 and overview["unresolved_calls"] == 3
