# Deploying LegacyNexus AI

The same code and images run locally and on Azure. One setting, `DEPLOYMENT_PROFILE`, says which; every
backing service is chosen by configuration, so moving between them is a configuration change, not a code change.

| Concern | Local (`DEPLOYMENT_PROFILE=local`) | Azure (`DEPLOYMENT_PROFILE=cloud`) |
|---|---|---|
| Main database (users, projects, code graph, **Code Q&A** + its conversations) | SQLite `data/legacynexus.db`, or PostgreSQL in Docker | Azure Database for PostgreSQL Flexible Server, database `legacynexus` |
| **Document Q&A** database (documents, conversations, history) | SQLite `data/docqa.db`, or database `legacynexus_docqa` in Docker | Same server, separate database `legacynexus_docqa` |
| Code Q&A vectors | File index `storage/projects/<id>/vectors`, or pgvector | pgvector table `lnx_chunk_vectors` in `legacynexus` |
| Document Q&A vectors | File index `storage/docqa/<id>/vectors`, or pgvector | pgvector table `lnx_doc_vectors` in `legacynexus_docqa` |
| Background analysis | Inside the API process | Service Bus queue + worker Container Apps Job (scales to zero) |
| Uploaded code, caches | Local disk / Docker volume | Azure Files share mounted at `/app/data` |
| Secrets | `backend/.env` | Key Vault, read through a managed identity |
| AI models | Ollama on the machine, or any provider | Azure OpenAI (`-WithAzureOpenAI`), or any provider set in Settings → AI models |
| Web + API | `npm run dev` + `uvicorn`, or docker compose | Container Apps (web public, API internal) |

Switching is configuration only:

| Setting | Local default | Cloud |
|---|---|---|
| Main database | `DATABASE_URL=sqlite:///./data/legacynexus.db` | `DB_HOST`, `DB_USER`, `DB_PASSWORD` (Key Vault), `DB_NAME` |
| Document Q&A database | derived: `docqa.db` next to the main file | `DOCQA_DB_NAME` on the same server; `DOCQA_DB_HOST/USER/PASSWORD` for a separate server; or `DOCQA_DATABASE_URL` |
| Vector stores | `VECTOR_STORE=auto`, `DOCQA_VECTOR_STORE=auto` | both `pgvector` |
| AI + embedding model | Settings page, or `AI_*` / `EMBEDDING_*` | `AI_*` / `EMBEDDING_*` from Bicep (key from Key Vault), applied on first start |
| Conversation context | `CONVERSATION_CONTEXT=auto` | same |

Schema migrations run automatically on start-up for both databases (each has its own migration history:
`backend/migrations` and `backend/migrations_docqa`). On PostgreSQL the Document Q&A database is created
automatically if it does not exist. Upgrading an existing installation moves its uploaded documents and their
question history out of the main database into the Document Q&A database once (main migration 0007).

## How the two Q&A modules are kept apart

| | Code Q&A | Document Q&A |
|---|---|---|
| Data | main database | Document Q&A database |
| Conversations | `qa_conversations` / `qa_entries` (main) | `docqa_conversations` / `docqa_entries` (Document Q&A) |
| Vectors | `lnx_chunk_vectors` or `storage/projects/…` | `lnx_doc_vectors` or `storage/docqa/…` |
| Retrieval | code chunks + knowledge graph facts | document passages |
| Shared | sign-in, the AI/embedding model choice, the LLM gateway (cache, budgets, cost log) | same |

Context-aware follow-ups work the same way in both, and cost no extra AI call to understand:
"what about CUSTOMER_MASTER?" is rewritten to the previous question with the new subject, and "who calls it?"
gets the previous subject in place of "it". The rewritten question drives the search (and, for code, the free
knowledge-graph answers). Only follow-ups send earlier turns to the model, capped by
`CONVERSATION_TURNS` and `CONVERSATION_CONTEXT_CHARS`, so stand-alone questions cost nothing extra.

## Local

```bash
# No Docker: SQLite + local files
cp backend/.env.local.example backend/.env
cd backend && uvicorn app.main:app --reload        # API on :8000
cd frontend && npm install && npm run dev          # UI on :5173

# Docker: PostgreSQL + pgvector, web UI on http://localhost:8080
docker compose up --build
docker compose --profile ai up --build             # also a local Ollama model server
```

## Azure

Prerequisites: Azure CLI, signed in (`az login`), Owner or Contributor + User Access Administrator on the
subscription (the template assigns roles to the app's managed identity).

```powershell
./infra/azure/deploy.ps1 -ResourceGroup rg-legacynexus-prod -Location westeurope -AdminEmail admin@contoso.com

# Lowest cost (fits Azure free-account grants) with Azure OpenAI chat + embeddings
./infra/azure/deploy.ps1 -ResourceGroup rg-legacynexus-dev -Env dev -AdminEmail admin@contoso.com -Sizing free -WithAzureOpenAI
```

`-Sizing free` uses PostgreSQL Burstable B1ms with 32 GB, lets the web and API apps scale to zero, and uses
Service Bus Basic. `-WithAzureOpenAI` creates an Azure OpenAI account with `gpt-4o-mini` and
`text-embedding-3-small` deployments (use `-OpenAILocation` if your region lacks them) and stores its key in
Key Vault. Without it, pick any provider in Settings → AI models.

The first run creates everything (about 15 minutes), builds both images in Azure Container Registry, and prints
the URL. Later runs build new images and roll them out; secrets are reused from Key Vault and never rotated.

What gets created (`main.bicep`):

- Container Apps environment with Log Analytics
- `web` (public, nginx + React) → `api` (internal FastAPI) → PostgreSQL Flexible Server 16 with pgvector,
  holding two databases: `legacynexus` (platform + Code Q&A) and `legacynexus_docqa` (Document Q&A)
- Optional Azure OpenAI account with a chat and an embedding deployment (`ai-api-key` in Key Vault)
- `worker` Container Apps Job, started by KEDA when the Service Bus queue has messages (0..N executions)
- Service Bus namespace + `analysis-jobs` queue (dead-lettering after 5 attempts)
- Storage account with an Azure Files share mounted into api and worker
- Key Vault (RBAC) holding `secret-key`, `db-password`, `bootstrap-admin-password`
- Azure Container Registry, and one user-assigned managed identity with AcrPull, Key Vault Secrets User,
  Service Bus Data Sender and Receiver

Check the deployment: `https://<web-url>/api/health/ready` lists each backing service and whether it answers.

## Technology selection

Chosen for free or low-cost entry, one engine to run, and a clear path to scale. Check current prices in the
Azure pricing calculator before committing; free grants depend on the account type.

| Component | Selected | Why | Considered |
|---|---|---|---|
| Databases (Code Q&A, Document Q&A) | Azure Database for PostgreSQL Flexible Server, two databases | Same engine as local Docker, so no code changes; migrations already exist; the Burstable B1ms size is in the Azure free account for 12 months; scales up to General Purpose and HA without changes; TLS, Entra ID and private access available | Azure SQL (free offer, but would need a second SQL dialect and vector story); Cosmos DB free tier (good scale, but a different data model and a rewrite of the queries) |
| Vector stores (one per module) | pgvector in each database (`lnx_chunk_vectors`, `lnx_doc_vectors`) | No extra service or bill; vectors share the database's backups, security and isolation; HNSW indexes can be added as volumes grow | Azure AI Search (free tier is 50 MB / 3 indexes, then a monthly charge per unit; worth it later for very large document estates); Cosmos DB vector search |
| Embedding model | Azure OpenAI `text-embedding-3-small` (cloud), Ollama `nomic-embed-text` (local) | Among the cheapest hosted embedding models and good for both code and prose; local option is free and keeps data on the machine. Each module stores its own vectors, tagged by model, so changing model only re-indexes | `text-embedding-3-large` (higher cost, marginal gain for this retrieval) |
| LLM for answers | Azure OpenAI `gpt-4o-mini` by default; any provider from Settings | Low price per token, JSON output, data stays in the Azure tenant. The gateway caches answers, scrubs secrets, enforces budgets and routes small/large models; follow-ups add history only when needed | Larger GPT models as the "large" tier; Anthropic or Gemini through Settings |
| Compute | Azure Container Apps (web, API) + Container Apps Job (worker) | Monthly free grant of vCPU-seconds and requests; scale to zero; same Docker images as local | App Service (no scale to zero on low tiers); AKS (more to operate) |
| Background jobs | Azure Service Bus queue | Basic tier is pay-per-operation; KEDA scales workers from the queue | Storage queues (fewer delivery guarantees) |
| Secrets | Azure Key Vault + managed identity | No secrets in files or images; pay per operation | App settings only |
| Files | Azure Files share | Mounted like a local folder; no code changes | Blob Storage (needs SDK changes) |
| Monitoring | Log Analytics (Container Apps logs) + `/api/health/ready` | Free monthly ingestion allowance; the readiness page checks both databases and both vector stores | Application Insights (add later for tracing) |

### Production hardening (next steps)

- Private networking: VNet-integrated Container Apps environment, PostgreSQL with private access, private
  endpoints for Key Vault, Storage and Service Bus (the template uses public endpoints restricted to Azure services).
- Entra ID sign-in (OIDC) in front of the web app.
- Zone redundancy and high availability on PostgreSQL for production tiers.
