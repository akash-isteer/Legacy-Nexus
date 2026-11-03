"""File explorer impact: pick a source file, see what changing it affects, optionally explained by AI."""
from app.llm.providers import LLMResult


def test_file_impact_and_explain(client, auth, sample_project, monkeypatch):
    base = f"/api/projects/{sample_project}"
    files = client.get(f"{base}/impact/files", headers=auth).json()["files"]
    assert files, "the sample should have files with analysable components"

    listed = {f["path"].lower(): f["path"] for f in client.get(f"{base}/files", headers=auth).json()}
    path = next(listed[p] for p in files if p in listed)  # explorer paths line up with the file list

    r = client.post(f"{base}/impact/file", json={"path": path}, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file"] == path and body["start"] and "impacted" in body

    csv = client.get(f"{base}/impact/export", params={"path": path}, headers=auth)
    assert csv.status_code == 200

    missing = client.post(f"{base}/impact/file", json={"path": "no/such/file.cbl"}, headers=auth)
    assert missing.status_code == 404

    # Explain needs exactly one of target / path
    assert client.post(f"{base}/impact/explain", json={}, headers=auth).status_code == 400

    # With a fake model configured, the AI wording comes back structured
    from app.llm import gateway
    from app.services import ai_config

    class Cfg:
        configured, provider, display_model, base_url, temperature = True, "fake", "fake-model", "", 0.0

    monkeypatch.setattr(ai_config, "to_llm_config", lambda row: Cfg())
    monkeypatch.setattr(gateway, "chain", lambda db, primary, tier: [primary])
    monkeypatch.setattr(gateway, "complete_json", lambda cfg, s, u: LLMResult(
        '{"summary": "Changing this touches payments.", "risks": ["Nightly batch"], "test_first": ["Posting"]}', "fake-model"))
    busy = next(listed[p] for p in files if p in listed and client.post(
        f"{base}/impact/file", json={"path": listed[p]}, headers=auth).json()["impacted"])
    out = client.post(f"{base}/impact/explain", json={"path": busy}, headers=auth)
    assert out.status_code == 200, out.text
    ans = out.json()
    assert ans["model"] == "fake-model" and ans["summary"] == "Changing this touches payments."
    assert ans["risks"] == ["Nightly batch"] and ans["test_first"] == ["Posting"]
