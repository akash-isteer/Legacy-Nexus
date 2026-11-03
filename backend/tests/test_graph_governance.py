"""Map overlays (hubs, dead code, blast radius) and rule review governance (reasons, approver, approved spec)."""


def test_graph_nodes_carry_hub_and_unused_flags(client, auth, sample_project):
    g = client.get(f"/api/projects/{sample_project}/graph", headers=auth).json()
    programs = {n["label"]: n for n in g["nodes"] if n["type"] == "program"}
    assert programs and all("criticality" in n and "fan_in" in n for n in programs.values())
    assert programs["ACCOUNT"]["fan_in"] == 1
    assert any(n.get("hub") for n in programs.values())
    assert programs["LEGACYTX"]["unused"] is True  # sample's dead program


def test_blast_radius_walks_dependents(client, auth, sample_project):
    d = client.get(f"/api/projects/{sample_project}/graph/node", params={"id": "program:INTEREST"}, headers=auth).json()
    b = d["blast_radius"]
    assert b["counts"]["program"] >= 1 and b["counts"]["job"] >= 1 and b["total"] == sum(b["counts"].values())
    ids = {s["id"] for s in b["sample"]}
    assert "program:ACCOUNT" in ids  # ACCOUNT calls INTEREST
    dead = client.get(f"/api/projects/{sample_project}/graph/node", params={"id": "program:LEGACYTX"}, headers=auth).json()
    assert dead["blast_radius"]["counts"]["program"] == 0


def test_overview_reports_decommission_savings(client, auth, sample_project):
    o = client.get(f"/api/projects/{sample_project}/overview", headers=auth).json()
    assert o["decommission"]["programs"] >= 1 and o["decommission"]["lines"] > 0
    assert 0 < o["decommission"]["share"] < 1


def test_review_reason_approver_and_spec(client, auth, sample_project):
    base = f"/api/projects/{sample_project}/rules"
    items = client.get(base, headers=auth).json()["items"]
    keep, drop = items[0]["id"], items[1]["id"]
    try:
        r = client.patch(f"{base}/{drop}/review", json={"status": "rejected", "reason": "technical", "note": "RC check"}, headers=auth).json()
        assert r["review_reason"] == "technical" and r["reviewed_by"]
        r = client.patch(f"{base}/{keep}/review", json={"status": "accepted", "reason": "obsolete", "note": "ok"}, headers=auth).json()
        assert r["review_reason"] == ""  # a reason only applies to rejections
        listed = {x["id"]: x for x in client.get(base, headers=auth).json()["items"]}
        assert listed[drop]["review_reason"] == "technical" and listed[drop]["reviewed_by"]

        spec = client.get(f"{base}/spec", headers=auth)
        assert spec.headers["content-type"].startswith("application/json")
        rules = {x["id"]: x for x in spec.json()["rules"]}  # other tests may approve rules on the shared sample
        assert keep in rules and drop not in rules
        assert rules[keep]["source"]["file"] and rules[keep]["approval"]["by"] and rules[keep]["approval"]["note"] == "ok"

        bad = client.patch(f"{base}/{drop}/review", json={"status": "rejected", "reason": "because"}, headers=auth)
        assert bad.status_code == 422
    finally:
        for rid in (keep, drop):
            client.patch(f"{base}/{rid}/review", json={"status": "candidate"}, headers=auth)
