"""Overview additions: sensitive data, shared-data hotspots, and changes since the last analysis."""
from app.engine.overview_extras import classify, diff_snapshots


def test_field_names_are_classified():
    assert classify("CUSTOMER_NAME") == "personal_name"
    assert classify("BRANCH_NAME") is None and classify("FILE-NAME") is None
    assert classify("WS-MAIL-ADDRESS") == "address" and classify("IP_ADDRESS") is None
    assert classify("custEmail") == "contact"
    assert classify("TAX_ID") == "government_id" and classify("SSN") == "government_id"
    assert classify("DATE-OF-BIRTH") == "birth_date"
    assert classify("CARD_NUMBER") == "card"
    assert classify("ACCOUNT_ID") == "bank_account"
    assert classify("USER_PASSWORD") == "credential"
    assert classify("CURRENT_BALANCE") is None and classify("TELEX_NUMBER") is None


def test_overview_lists_sensitive_data_and_hotspots(client, auth, sample_project):
    o = client.get(f"/api/projects/{sample_project}/overview", headers=auth).json()
    sd = o["sensitive_data"]
    assert "CUSTOMER_MASTER" in sd["tables"] and sd["fields"] >= 3
    names = next(c for c in sd["categories"] if c["key"] == "personal_name")["fields"]
    assert any(f["field"] == "CUSTOMER_NAME" and "CUSTOMER" in f["used_by"] for f in names)

    hot = {h["table"]: h for h in o["data_hotspots"]}
    assert hot["ACCOUNT_MASTER"]["shared_write"] and len(hot["ACCOUNT_MASTER"]["writers"]) >= 2
    assert not hot["PAYMENT_LEDGER"]["shared_write"]
    assert "changes" in o


def test_changes_since_last_run():
    assert diff_snapshots(None, {"files": {}, "components": {}, "rules": {}}) == {"baseline": True}
    prev = {"files": {"a.cbl": "1", "b.cbl": "2"}, "components": {"program": ["A", "B"]}, "rules": {"r1": "Rule one"}}
    cur = {"files": {"a.cbl": "9", "c.cbl": "3"}, "components": {"program": ["A", "C"]}, "rules": {"r2": "Rule two"}}
    d = diff_snapshots(prev, cur)
    assert d["files"] == {"added": ["c.cbl"], "removed": ["b.cbl"], "changed": ["a.cbl"]}
    assert d["components"] == [{"kind": "program", "label": "programs", "added": ["C"], "removed": ["B"]}]
    assert d["rules"]["added"] == 1 and d["rules"]["added_sample"] == ["Rule two"] and d["rules"]["removed"] == 1
    assert diff_snapshots(cur, cur)["unchanged"] is True
