# LegacyNexus AI

**Legacy application comprehension and modernization platform.**
Upload COBOL, copybooks, JCL, DB2 SQL/DDL, stored procedures, OpenAPI specs and notes. LegacyNexus builds a
Legacy Knowledge Graph from them and gives you: grounded Q&A with verified citations, an automatic dependency
graph, field-level impact analysis, extracted business rules, data lineage, seven generated documentation sets
and a modernization plan with parity tests.

Parsing and analysis are **deterministic** (real parsers, no AI tokens). The LLM is used **only** for answering
questions in *Ask the code*, from a handful of retrieved excerpts — never whole files.

---

## 1. Run it locally (development)

### Prerequisites
- **Python 3.11 or 3.12**
- **Node.js 20+** (with npm)

### 1.1 Backend (FastAPI) — terminal 1

macOS / Linux:
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then edit .env (see section 3)
uvicorn app.main:app --reload --port 8000
```

Windows (PowerShell):
```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # then edit .env (see section 3)
uvicorn app.main:app --reload --port 8000
```

Check: <http://localhost:8000/api/health> returns `{"status":"ok", ...}`.
Interactive API docs: <http://localhost:8000/api/docs>.

### 1.2 Frontend (React + Vite) — terminal 2
```bash
cd frontend
npm install
cp .env.example .env            # Windows: copy .env.example .env   (optional; defaults work)
npm run dev
```
Open <http://localhost:5173>. Vite forwards `/api` to the backend on port 8000.

### 1.3 Sign in (demo user)
| Field    | Value (from `backend/.env`) |
|----------|-----------------------------|
| Email    | `admin@legacynexus.ai`      |
| Password | `LegacyNexus@2026`          |

The administrator is created on first start **only if the user table is empty**, from
`BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD`. Change both before any shared deployment.
If you change them after the first start, delete `backend/data/legacynexus.db` (or create the user again).

### 1.4 First five minutes
1. Sign in. A welcome guide explains the four steps; each page has its own spotlight guide (replay with **Guide**).
2. Click **Load sample application** (a small COBOL/DB2 core-banking system shipped in `backend/sample_data`).
3. Wait ~1 second for the analysis, then use the buttons on the project page.
4. Optional: **Settings → AI model** to choose a provider/model and paste its key, then **Test connection**.

---

## 2. Run it with Docker (production-style: PostgreSQL + nginx)

```bash
cp backend/.env.example backend/.env      # set SECRET_KEY, admin email/password
export POSTGRES_PASSWORD='a-strong-password'   # Windows PowerShell: $env:POSTGRES_PASSWORD='...'
docker compose up --build
```
Open <http://localhost:8080>. Data lives in the `pgdata` and `storage` volumes.

---

## 3. Configuration

### Backend (`backend/.env`)
| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | — (required, ≥32 chars) | Signs login tokens and encrypts stored AI keys. Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Changing it invalidates sessions and saved AI keys. |
| `ENVIRONMENT` | `development` | In `development` the sign-in page shows which demo email to use (never the password). Use `production` otherwise. |
| `DATABASE_URL` | SQLite in `backend/data/` | PostgreSQL: `postgresql+psycopg://user:pass@host:5432/legacynexus` |
| `STORAGE_DIR` | `./data/storage` | Where uploaded sources are stored (relative paths resolve from `backend/`). |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated browser origins allowed to call the API. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `480` | Session length. |
| `MAX_UPLOAD_MB` | `2048` | Largest upload request (e.g. a zipped release). Uploads stream to disk, not memory. Keep nginx `client_max_body_size` in step. |
| `MAX_EXTRACTED_MB` / `MAX_FILES_PER_PROJECT` | `4096` / `200000` | Limits on the **source files kept** from archives; binaries, images, installers and other non-source files are skipped and do not count. |
| `MAX_SOURCE_FILE_MB` | `20` | Single files larger than this are skipped (data dumps, generated listings). |
| `BOOTSTRAP_ADMIN_EMAIL` / `_PASSWORD` / `_NAME` | demo values | First administrator, created only when no users exist. |
| `LLM_REQUEST_TIMEOUT_SECONDS` | `90` | Timeout for model calls. |
| `QA_TOP_K`, `QA_CONTEXT_CHAR_BUDGET`, `QA_MIN_RETRIEVAL_SCORE` | `8`, `14000`, `1.5` | Q&A defaults; also editable in Settings. |
| `VECTOR_STORE` | `auto` | Semantic-search vectors: `auto` uses pgvector when the database is PostgreSQL with the extension, otherwise a local file index; or force `pgvector` / `file`. |
| `EMBEDDING_REQUEST_TIMEOUT_SECONDS` | `120` | Timeout for embedding calls. |

### Frontend (`frontend/.env`)
| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `/api` | API location. Use a full URL if the API is on another host. |
| `VITE_DEV_API_PROXY` | `http://localhost:8000` | Dev-server proxy target. |
| `VITE_DEMO_VIDEO_URL` | empty | Optional recorded video for the landing page; empty shows the built-in interactive demo. |

### AI model (in the UI: **Settings → AI model**, admin only)
Supported providers: **Anthropic Claude**, **OpenAI**, **Azure OpenAI** (endpoint + deployment + API version),
**Google Gemini**, **Ollama** (local, no key, no data leaves your network) and any **OpenAI-compatible** endpoint
(vLLM, LM Studio, corporate gateways). Model names are suggestions; type any model id your account can use.
Keys are encrypted at rest (Fernet, derived from `SECRET_KEY`) and only shown masked.
Without a model, *Ask the code* still returns the static-analysis evidence for each question.

### Semantic search (in the UI: **Settings → Semantic search**, admin only)
Optional hybrid search: questions are matched by **keyword** (BM25 + entity and dependency-graph boosts) **and by
meaning** (vector embeddings), and the two rankings are merged with Reciprocal Rank Fusion. This lets business
language (*"where are customers charged for going negative?"*) find code that uses different words (`WS-OVERDRAFT-FEE`).

1. Choose an embedding provider: **Ollama** (local; e.g. `ollama pull nomic-embed-text`), OpenAI, Azure OpenAI,
   Gemini or any OpenAI-compatible endpoint. Tick **Enable semantic search**, save, then **Test connection**.
2. Open a project's **Ask the code** page and click **Build semantic index** (new analyses are indexed automatically).
   The Search mode panel shows *Hybrid* when the index is ready.
3. Vectors are stored in **PostgreSQL pgvector** (the Docker image includes it) or, with SQLite, a local file index.

Notes:
- Building the index sends **every code chunk** to the embedding provider. For confidential code use Ollama or a
  private endpoint. Answers still send only the top excerpts to the AI model.
- **Minimum similarity** (default 0.35) depends on the model; raise it if unrelated questions get matches, lower it
  if paraphrased questions are missed.
- If the embedding service is down, questions automatically fall back to keyword search. Changing the embedding
  model or re-analysing marks the index stale until it is rebuilt.
- To install pgvector on an existing PostgreSQL: `CREATE EXTENSION vector;` (package `postgresql-16-pgvector`).

---

## 4. How the five problem statements are covered

| Problem | Where | How |
|---|---|---|
| 1. Nobody understands the old code | **Ask the code** | Entity linking + hybrid retrieval (BM25 + optional semantic vectors, fused) + knowledge-graph facts → LLM with a strict JSON contract → citation, line-range and identifier validation → calibrated confidence score → abstains with *"Not found in the provided code"*. Example: *"Where is interest calculated?"* → `INTEREST.CBL`, called from `ACCOUNT.CBL`, using `INTEREST_RATE` from `ACCOUNT_MASTER`. |
| 2. Dependencies are hard to identify | **Dependency graph** | Job → program → program → table → program → dataset/report, plus APIs, copybooks, stored procedures and external calls (e.g. MQ). Every edge has a file:line. Export PNG. |
| 3. Documentation is missing | **Documentation** | Application overview, module, API, data-flow, business-rule, database mapping (with CRUD matrix) and dependency docs. Download each as Markdown or all as a zip. |
| 4. Modernization is risky | **Business rules**, **Modernization** | Rules with plain-language text, resolved constants and source lines; parity test pack (CSV / Gherkin); readiness score, 6R strategy per program, migration waves, target architecture (Java/.NET/Python × PostgreSQL/Azure SQL/Oracle/MySQL × Azure/AWS/GCP/on-prem), DDL conversion via sqlglot, risk register. |
| 5. Impact analysis | **Impact analysis**, **Data lineage** | Field-level data *and control* flow through MOVE/COMPUTE, SQL, CALL parameters, files and APIs. `CUSTOMER_ADDRESS` → `CUSTOMER` → `ACCOUNT` → `PAYMENT` → `REPORT12` / `API_CUSTOMER`, with severity and evidence per hop. Export CSV. |

### Answer format (requirement 10)
Every Q&A response is structured JSON:
```json
{
  "answer": "…", "not_found": false, "confidence": 0.86, "confidence_label": "High",
  "confidence_breakdown": [{"name": "Valid citations", "weight": 0.25, "value": 1.0}, …],
  "claims": [{"text": "…", "supported": true,
              "citations": [{"file": "cobol/INTEREST.CBL", "line_start": 60, "line_end": 64, "valid": true}]}],
  "evidence": [ … retrieved excerpts … ],
  "validation": {"citations_total": 3, "citations_valid": 3, "unknown_identifiers": []}
}
```
A citation is valid only if the file exists, the line range exists, and the lines were part of the retrieved
evidence. Answers with no valid citation, or below the confidence threshold, become *Not found in the provided code*.

---

## 4a. Document Q&A — role-specific answers over one uploaded document

A separate, self-contained feature (sidebar: **Document Q&A**, route `/doc-qa`). It is additive and does
not change any of the five features above or their code.

Upload one **PDF, Word (.docx), text (.txt) or Markdown (.md)** document and ask questions about it from four
perspectives, chosen in a dropdown. The same document is the single source of truth for every role; the role
changes how the answer is explained, never the underlying facts.

| Role | The answer emphasises |
|---|---|
| **End User** | Plain-language purpose, features, step-by-step how-to, expected behaviour, documented errors/limits. |
| **Tester** | Test scenarios and cases (preconditions/steps/expected), positive/negative/boundary, edge cases, risks and coverage gaps — documented defects vs. inferred ones kept separate. |
| **Developer** | Architecture, logic, APIs, data models, validation rules, dependencies, config, error handling and constraints. |
| **Business Person** | Business requirements, rules, processes, stakeholders, outcomes, scope and acceptance criteria — in business terms. |

**How it works.** Extract text (pypdf / python-docx) → chunk with page/section metadata → optionally embed
(reusing the configured embedding model) → per question, retrieve the most relevant passages (semantic +
keyword, fused with RRF; keyword-only when no embedding model is set) → build a **role-specific system prompt**
(shared grounding rules + role style + strict JSON contract) → ask the configured model → validate every
citation against the passages that were actually sent → calibrate confidence.

**Grounding & safety.** The model may use only the retrieved passages — never outside knowledge. Each statement
must cite a passage by its `[S#]` number; a citation to a passage that was not sent is rejected. If nothing
relevant is found, or the answer cannot be verified, the response is exactly *"The uploaded document does not
contain sufficient information to answer this question."* Uploaded documents are treated as untrusted content:
instructions embedded inside a document cannot change the role, override the system prompt, or bypass grounding.
Documents are **isolated per user** — you can only see and query your own uploads.

Switch perspective on any answer (re-asks the same question as another role, no re-upload), or **Compare all
perspectives** to see all four side by side. Without a configured AI model the feature still works and returns
the most relevant passages instead of a written answer.

Endpoints (all require auth, under the API prefix): `GET /doc-qa/roles`, `GET|POST /doc-qa/documents`,
`GET|DELETE /doc-qa/documents/{id}`, `POST /doc-qa/documents/{id}/ask`, `POST /doc-qa/documents/{id}/compare`,
`GET /doc-qa/documents/{id}/history`. Limits and defaults: see `DOCQA_*` in `backend/.env.example`.

---

## 5. Architecture

```
frontend/  React 19 + TypeScript (Vite). Cytoscape + ELK for graphs, marked + DOMPurify for docs.
backend/
  app/
    main.py            FastAPI app, security headers, optional static hosting of the built UI
    config.py          Settings from environment / .env
    models.py          Users, projects, files, analysis artifacts, Q&A history, AI settings, audit log
    routers/           auth, projects, analysis (graph/rules/lineage/impact/docs/modernization), qa, settings
    parsers/           COBOL (fixed & free format), JCL, SQL/DDL/procedures (sqlglot), OpenAPI
    engine/            model → knowledge graph → lineage → rules → insights → docs → retrieval → Q&A
    llm/providers.py   Anthropic, OpenAI, Azure OpenAI, Gemini, Ollama, OpenAI-compatible (plain HTTPS)
    llm/embeddings.py  Embedding adapters for semantic search (Ollama, OpenAI, Azure OpenAI, Gemini, compatible)
    engine/vectors.py  Vector stores: PostgreSQL pgvector, or a NumPy file index
    services/          storage (safe uploads), artifacts, background analysis, AI config, audit
  sample_data/         Sample core-banking application (COBOL, copybook, JCL, DB2 DDL, procedure, API, notes)
  tests/               45 tests: parsers, engine, all five problem statements, API, providers, hybrid search, pgvector
```

Supported inputs: `.cbl .cob .pco .sqb` (COBOL), `.cpy` (copybooks), `.jcl .prc` (JCL), `.sql .ddl .pls`
(SQL/DDL/procedures), `.yaml .yml .json` (OpenAPI/Swagger), `.md .txt` (notes), and extension-less mainframe
members detected by content. Upload individual files, whole folders or a `.zip`.

---

## 6. Tests
```bash
cd backend
pytest -q
# optional: also test the pgvector store against a PostgreSQL that has the extension
LNX_TEST_PG_URL="postgresql+psycopg://user:pass@localhost:5432/legacynexus" pytest -q
```

## 7. Production checklist
- Set `ENVIRONMENT=production`, a random `SECRET_KEY`, and a strong admin password; sign in and keep it safe.
- Use PostgreSQL (`DATABASE_URL`) and persistent storage for `STORAGE_DIR`.
- Serve over HTTPS behind your load balancer; set `CORS_ORIGINS` to your real UI origin.
- For confidential code, use **Ollama** or a private/Azure endpoint so source excerpts never leave your network.
- Analyses run as in-process background tasks. For very large estates or multiple API replicas, move
  `services/analysis_runner.analyze_project` behind a job queue (Celery, RQ or Arq); it already takes only a project id.
- Back up the database and storage volume; the audit log (Settings → Audit log) records sign-ins, uploads,
  questions, impact analyses and setting changes.

## 8. Troubleshooting
| Symptom | Fix |
|---|---|
| Backend exits with "secret_key … field required" | Create `backend/.env` from `.env.example`. |
| "Cannot reach the LegacyNexus server" in the UI | Start the backend on port 8000, or set `VITE_DEV_API_PROXY`. |
| Sign-in fails with the demo password | The admin is only created on an empty database. Delete `backend/data/legacynexus.db` and restart. |
| Saved AI key stopped working | `SECRET_KEY` changed; re-enter the key in Settings. |
| Search mode stays *Keyword* | Enable semantic search in Settings, test the connection, then **Build semantic index** on the Ask page. A *stale* message means the model changed or the code was re-analysed: rebuild. |
| *Ask* returns evidence but no written answer | No model configured, or the call failed (the warning shows why). Use **Test connection**. |
