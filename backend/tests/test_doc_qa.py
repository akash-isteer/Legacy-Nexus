"""Document-based role-specific Q&A: upload, extraction, retrieval, grounding and role perspectives.

App modules are imported lazily inside the tests, never at module top level: conftest's ``client``
fixture sets the test environment before the app (and its settings) are first imported, so importing
app modules at collection time would bind settings to the .env file and break the admin login.
"""
import json

import pytest

SPEC = b"""# Login Feature
The login screen lets a user sign in with an email address and a password.
If the password is entered incorrectly three times the account is locked for 15 minutes.

## Password rules
Passwords must be at least 8 characters long and contain at least one number.

# Reporting
Managers can export the monthly activity report as a CSV file.
A single export is limited to a maximum of 10000 rows.
"""


def _upload(client, auth, name="spec.md", data=SPEC, ctype="text/markdown"):
    r = client.post("/api/doc-qa/documents", headers=auth, files={"file": (name, data, ctype)})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture(scope="module")
def document(client, auth):
    return _upload(client, auth)["id"]


def _configure_model(client, auth, provider="anthropic", model="claude-sonnet-5-5"):
    client.put("/api/settings/ai", json={"provider": provider, "model": model, "api_key": "sk-ant-test-123456"},
               headers=auth)


def _clear_model(client, auth):
    client.put("/api/settings/ai", json={"provider": "none", "clear_api_key": True}, headers=auth)


def _mock_llm(monkeypatch, payload: dict):
    from app.llm.providers import LLMResult
    from app.services import doc_qa

    def fake(cfg, system, user):
        return LLMResult(text=json.dumps(payload), model="mock-model", input_tokens=10, output_tokens=5, latency_ms=12)
    monkeypatch.setattr(doc_qa, "complete_json", fake)


# ------------------------------------------------------------------ basics


def test_roles_endpoint(client, auth):
    roles = client.get("/api/doc-qa/roles", headers=auth).json()
    assert [r["id"] for r in roles] == ["end_user", "tester", "developer", "business"]
    assert all(r["label"] for r in roles)


def test_requires_auth(client):
    assert client.get("/api/doc-qa/documents").status_code == 401


def test_upload_extracts_text(client, auth, document):
    d = client.get(f"/api/doc-qa/documents/{document}", headers=auth).json()
    assert d["format"] == "md" and d["chunk_count"] >= 1 and d["char_count"] > 100
    assert d["status"] == "ready"


def _make_pdf(lines):
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
    ]
    text = ("BT /F1 18 Tf 72 720 Td 16 TL " + " ".join("(%s) Tj T*" % x for x in lines) + " ET").encode()
    objs.append(b"<</Length %d>>stream\n%s\nendstream" % (len(text), text))
    objs.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj" % i + o + b"endobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref_pos)
    return bytes(out)


def test_pdf_upload_and_ask(client, auth):
    pdf = _make_pdf(["Payment Processing", "Payments above 10000 require manager approval."])
    doc = _upload(client, auth, name="payments.pdf", data=pdf, ctype="application/pdf")
    assert doc["format"] == "pdf" and doc["page_count"] == 1 and doc["char_count"] > 30
    _clear_model(client, auth)
    r = client.post(f"/api/doc-qa/documents/{doc['id']}/ask",
                    json={"question": "When is manager approval required for payments?", "role": "business"}, headers=auth).json()
    assert r["evidence"] and r["evidence"][0]["page"] == 1
    client.delete(f"/api/doc-qa/documents/{doc['id']}", headers=auth)


def test_docx_upload_extracts_sections(client, auth):
    import io

    import docx
    d = docx.Document()
    d.add_heading("Onboarding", level=1)
    d.add_paragraph("New users complete a KYC check before their account is activated.")
    buf = io.BytesIO()
    d.save(buf)
    doc = _upload(client, auth, name="policy.docx", data=buf.getvalue(),
                  ctype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert doc["format"] == "docx" and doc["char_count"] > 30
    client.delete(f"/api/doc-qa/documents/{doc['id']}", headers=auth)


def test_unsupported_format_rejected(client, auth):
    r = client.post("/api/doc-qa/documents", headers=auth, files={"file": ("x.exe", b"MZ....", "application/octet-stream")})
    assert r.status_code == 400


def test_empty_document_rejected(client, auth):
    r = client.post("/api/doc-qa/documents", headers=auth, files={"file": ("empty.txt", b"   ", "text/plain")})
    assert r.status_code == 422


# ------------------------------------------------------------------ answering without a model


def test_ask_without_model_gives_evidence(client, auth, document):
    _clear_model(client, auth)
    r = client.post(f"/api/doc-qa/documents/{document}/ask",
                    json={"question": "What happens when the password is wrong?", "role": "end_user"}, headers=auth).json()
    assert r["mode"] == "evidence_only" and r["evidence"]
    assert r["role"] == "end_user" and r["role_label"] == "End User"
    assert 0 <= r["confidence"] <= 1


def test_empty_question_rejected(client, auth, document):
    r = client.post(f"/api/doc-qa/documents/{document}/ask", json={"question": "  ", "role": "end_user"}, headers=auth)
    assert r.status_code == 422


def test_invalid_role_rejected(client, auth, document):
    r = client.post(f"/api/doc-qa/documents/{document}/ask", json={"question": "hello there", "role": "wizard"}, headers=auth)
    assert r.status_code == 422


# ------------------------------------------------------------------ answering with a (mocked) model


def test_llm_answer_is_grounded(client, auth, document, monkeypatch):
    _configure_model(client, auth)
    _mock_llm(monkeypatch, {"answer": "Sign in with your email and password.", "not_found": False,
                            "confidence": 0.9, "citations": [{"source": 1, "note": "login"}], "missing": None})
    try:
        r = client.post(f"/api/doc-qa/documents/{document}/ask",
                        json={"question": "What are the password rules for login?", "role": "end_user"}, headers=auth).json()
        assert r["mode"] == "llm" and not r["not_found"]
        assert r["citations"] and r["citations"][0]["valid"]
        assert r["validation"]["citations_valid"] == 1
    finally:
        _clear_model(client, auth)


def test_fabricated_citation_is_rejected(client, auth, document, monkeypatch):
    """A citation that points at an excerpt we never sent must not be accepted as grounded."""
    from app.services import doc_qa
    _configure_model(client, auth)
    _mock_llm(monkeypatch, {"answer": "The system integrates with SAP via REST.", "not_found": False,
                            "confidence": 0.95, "citations": [{"source": 99}], "missing": None})
    try:
        # A question with vocabulary in the document so excerpts ARE retrieved and the model IS called;
        # the model then returns a citation to an excerpt (S99) that was never sent.
        r = client.post(f"/api/doc-qa/documents/{document}/ask",
                        json={"question": "What are the password rules?", "role": "developer"}, headers=auth).json()
        assert r["mode"] == "llm"
        assert r["validation"]["citations_valid"] == 0
        assert r["not_found"] and r["answer"] == doc_qa.INSUFFICIENT
    finally:
        _clear_model(client, auth)


def test_model_not_found_flag_respected(client, auth, document, monkeypatch):
    from app.services import doc_qa
    _configure_model(client, auth)
    _mock_llm(monkeypatch, {"answer": doc_qa.INSUFFICIENT, "not_found": True, "confidence": 0.0, "citations": []})
    try:
        r = client.post(f"/api/doc-qa/documents/{document}/ask",
                        json={"question": "What is the refund policy?", "role": "business"}, headers=auth).json()
        assert r["not_found"] and r["answer"] == doc_qa.INSUFFICIENT
    finally:
        _clear_model(client, auth)


def test_same_question_all_roles_same_facts(client, auth, document, monkeypatch):
    """Switching role reuses the same document and retrieves the same evidence; only perspective changes."""
    from app.services import role_prompts
    _configure_model(client, auth)
    _mock_llm(monkeypatch, {"answer": "role-specific wording", "not_found": False, "confidence": 0.8,
                            "citations": [{"source": 1}], "missing": None})
    try:
        question = "What are the password rules?"
        sent = {}
        for role in role_prompts.ROLES:
            r = client.post(f"/api/doc-qa/documents/{document}/ask",
                            json={"question": question, "role": role}, headers=auth).json()
            assert r["role"] == role
            sent[role] = r["retrieval"]["chunks_sent"]
        # Same question + same document => identical retrieval regardless of role.
        assert len(set(sent.values())) == 1
    finally:
        _clear_model(client, auth)


def test_compare_returns_all_roles(client, auth, document):
    r = client.post(f"/api/doc-qa/documents/{document}/compare",
                    json={"question": "How are reports exported?"}, headers=auth).json()
    assert [a["role"] for a in r["answers"]] == ["end_user", "tester", "developer", "business"]


# ------------------------------------------------------------------ history, listing, deletion, isolation


def test_history_records_questions(client, auth, document):
    client.post(f"/api/doc-qa/documents/{document}/ask",
                json={"question": "A unique history probe question?", "role": "tester"}, headers=auth)
    hist = client.get(f"/api/doc-qa/documents/{document}/history", headers=auth).json()
    assert any(h["question"] == "A unique history probe question?" and h.get("role") == "tester" for h in hist)


def test_delete_document(client, auth):
    doc = _upload(client, auth, name="temp.txt", data=b"Some disposable content for deletion test.", ctype="text/plain")
    assert client.delete(f"/api/doc-qa/documents/{doc['id']}", headers=auth).status_code == 204
    assert client.get(f"/api/doc-qa/documents/{doc['id']}", headers=auth).status_code == 404


def test_documents_isolated_between_users(client, auth, document):
    """A second user must not see or query the first user's document."""
    from app.database import SessionLocal
    from app.models import User
    from app.security import hash_password

    with SessionLocal() as db:
        if db.query(User).filter_by(email="second@test.local").first() is None:
            db.add(User(email="second@test.local", full_name="Second", password_hash=hash_password("Second@123"),
                        role="analyst", preferences={}))
            db.commit()
    tok = client.post("/api/auth/login", json={"email": "second@test.local", "password": "Second@123"}).json()["access_token"]
    other = {"Authorization": f"Bearer {tok}"}
    assert client.get(f"/api/doc-qa/documents/{document}", headers=other).status_code == 404
    assert client.post(f"/api/doc-qa/documents/{document}/ask",
                       json={"question": "anything at all", "role": "end_user"}, headers=other).status_code == 404
    assert client.get("/api/doc-qa/documents", headers=other).json() == []
