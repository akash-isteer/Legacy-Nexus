"""API tests: authentication, project lifecycle and every feature endpoint."""
import io
import zipfile


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_login_rejects_bad_password(client):
    r = client.post("/api/auth/login", json={"email": "admin@test.local", "password": "wrong"})
    assert r.status_code == 401


def test_requires_auth(client):
    assert client.get("/api/projects").status_code == 401


def test_overview(client, auth, sample_project):
    r = client.get(f"/api/projects/{sample_project}/overview", headers=auth)
    assert r.status_code == 200
    o = r.json()["overview"]
    assert o["programs_analysed"] == 6 and o["business_rules"] > 0


def test_graph_and_node(client, auth, sample_project):
    g = client.get(f"/api/projects/{sample_project}/graph", headers=auth).json()
    assert any(n["id"] == "job:NIGHTLY" for n in g["nodes"])
    d = client.get(f"/api/projects/{sample_project}/graph/node", params={"id": "program:ACCOUNT"}, headers=auth).json()
    assert d["stats"]["callers"] == ["PAYMENT"]


def test_impact_by_name(client, auth, sample_project):
    r = client.post(f"/api/projects/{sample_project}/impact", json={"target": "CUSTOMER_ADDRESS"}, headers=auth).json()
    assert not r["ambiguous"]
    labels = {i["label"] for i in r["impacted"]}
    assert {"CUSTOMER", "ACCOUNT", "PAYMENT", "REPORT12", "API_CUSTOMER"} <= labels


def test_examples_and_graph_filters(client, auth, sample_project):
    ex = client.get(f"/api/projects/{sample_project}/lineage/examples", headers=auth).json()
    assert ex and ex[0]["type"] == "column" and not ex[0]["name"].endswith("_ID")
    g = client.get(f"/api/projects/{sample_project}/graph", headers=auth).json()
    assert not any(e["type"] == "REFERENCES" for e in g["edges"])
    g = client.get(f"/api/projects/{sample_project}/graph", params={"include_fk": True}, headers=auth).json()
    assert any(e["type"] == "REFERENCES" for e in g["edges"])


def test_impact_ambiguous(client, auth, sample_project):
    r = client.post(f"/api/projects/{sample_project}/impact", json={"target": "CUSTOMER_ID"}, headers=auth).json()
    assert r["ambiguous"] and len(r["options"]) >= 2


def test_rules_and_export(client, auth, sample_project):
    r = client.get(f"/api/projects/{sample_project}/rules", params={"category": "Validation"}, headers=auth).json()
    assert r["total"] > 0 and all(x["category"] == "Validation" for x in r["items"])
    csv = client.get(f"/api/projects/{sample_project}/rules/export", headers=auth)
    assert csv.headers["content-type"].startswith("text/csv") and "BR-" in csv.text


def test_docs(client, auth, sample_project):
    docs = client.get(f"/api/projects/{sample_project}/docs", headers=auth).json()
    assert len(docs) == 7
    one = client.get(f"/api/projects/{sample_project}/docs/database-mapping", headers=auth).json()
    assert "CUSTOMER_ADDRESS" in one["markdown"]
    z = client.get(f"/api/projects/{sample_project}/docs/export", headers=auth)
    assert len(zipfile.ZipFile(io.BytesIO(z.content)).namelist()) == 7


def test_lineage(client, auth, sample_project):
    r = client.get(f"/api/projects/{sample_project}/lineage", params={"node": "column:CUSTOMER_MASTER.CUSTOMER_ADDRESS"},
                   headers=auth).json()
    assert any(n["id"] == "field:CUSTOMER.LK-CUST-ADDRESS" for n in r["nodes"])


def test_modernization(client, auth, sample_project):
    r = client.post(f"/api/projects/{sample_project}/modernization",
                    json={"language": "java", "database": "postgresql", "platform": "azure"}, headers=auth).json()
    assert r["readiness"]["score"] > 0 and r["waves"][0]["name"].startswith("Retire")
    g = client.get(f"/api/projects/{sample_project}/modernization/parity", params={"format": "gherkin"}, headers=auth)
    assert "Scenario:" in g.text


def test_ask_without_model_gives_evidence(client, auth, sample_project):
    r = client.post(f"/api/projects/{sample_project}/ask", json={"question": "Where is interest calculated?"},
                    headers=auth).json()
    assert r["mode"] == "evidence_only" and r["evidence"]
    assert 0 <= r["confidence"] <= 1
    hist = client.get(f"/api/projects/{sample_project}/qa/history", headers=auth).json()
    assert hist[-1]["question"] == "Where is interest calculated?"


def test_ai_settings_masks_key(client, auth):
    r = client.put("/api/settings/ai", json={"provider": "anthropic", "model": "claude-sonnet-5-5",
                                             "api_key": "sk-ant-test-1234567890"}, headers=auth).json()
    assert r["api_key_set"] and "1234567890" not in r["api_key_masked"]
    assert client.get("/api/settings/ai", headers=auth).json()["configured"]
    client.put("/api/settings/ai", json={"provider": "none", "clear_api_key": True}, headers=auth)


def test_release_bundle_keeps_only_source(client, auth):
    """A release zip with binaries keeps only analysable sources; binaries do not count toward limits."""
    pid = client.post("/api/projects", json={"name": "Release bundle"}, headers=auth).json()["id"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("FastTrack/src/PAYCALC.cbl", "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. PAYCALC.\n"
                                                 "       PROCEDURE DIVISION.\n       MAIN.\n           STOP RUN.\n")
        zf.writestr("FastTrack/jcl/RUNPAY", "//RUNPAY JOB (X)\n//S1 EXEC PGM=PAYCALC\n")
        zf.writestr("FastTrack/bin/setup.exe", b"MZ" + b"\0" * 5000)
        zf.writestr("FastTrack/lib/app.jar", b"PK" + b"\0" * 5000)
        zf.writestr("FastTrack/docs/manual.pdf", b"%PDF" + b"\0" * 5000)
    r = client.post(f"/api/projects/{pid}/files", files=[("files", ("release.zip", buf.getvalue(), "application/zip"))],
                    headers=auth)
    assert r.status_code == 200, r.text
    assert "2 source file(s) uploaded, 3 non-source file(s) skipped" in r.json()["stage"]
    files = {f["path"] for f in client.get(f"/api/projects/{pid}/files", headers=auth).json()}
    assert files == {"FastTrack/src/PAYCALC.cbl", "FastTrack/jcl/RUNPAY"}


def test_binary_only_upload_is_rejected(client, auth):
    pid = client.post("/api/projects", json={"name": "Binaries"}, headers=auth).json()["id"]
    r = client.post(f"/api/projects/{pid}/files", files=[("files", ("tool.exe", b"MZ" * 100, "application/octet-stream"))],
                    headers=auth)
    assert r.status_code == 400 and "No analysable source files" in r.json()["detail"]


def test_upload_zip_and_reject_traversal(client, auth):
    pid = client.post("/api/projects", json={"name": "Upload test"}, headers=auth).json()["id"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("src/HELLO.cbl", "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. HELLO.\n"
                                     "       PROCEDURE DIVISION.\n       MAIN.\n           STOP RUN.\n")
    r = client.post(f"/api/projects/{pid}/files", files=[("files", ("code.zip", buf.getvalue(), "application/zip"))],
                    headers=auth)
    assert r.status_code == 200
    bad = client.post(f"/api/projects/{pid}/files", files=[("files", ("../../evil.cbl", b"x", "text/plain"))], headers=auth)
    assert bad.status_code == 400
    assert client.post(f"/api/projects/{pid}/analyze", headers=auth).status_code == 202
    assert client.get(f"/api/projects/{pid}", headers=auth).json()["status"] == "ready"
    content = client.get(f"/api/projects/{pid}/files/content", params={"path": "src/HELLO.cbl"}, headers=auth).json()
    assert content["line_count"] >= 5
