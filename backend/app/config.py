"""Application settings, loaded from environment variables or a .env file."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEMO_SECRET_PREFIX = "dev-only-"  # prefix of the sample SECRET_KEY in .env.example
DEMO_ADMIN_PASSWORDS = {"LegacyNexus@2026"}  # demo password shipped in .env.example


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "LegacyNexus AI"
    environment: str = "development"
    api_prefix: str = "/api"

    # Where the app runs. Every backing service is chosen by configuration, so the same build runs in both:
    #   local = SQLite or a local PostgreSQL, jobs in the API process, files on local disk, Ollama
    #   cloud = Azure Database for PostgreSQL (+pgvector), Service Bus workers, Azure Files, Key Vault secrets
    deployment_profile: str = "local"

    # Security
    secret_key: str = Field(..., min_length=32, description="Signs JWTs and encrypts stored API keys")
    access_token_expire_minutes: int = 480
    cors_origins: str = "http://localhost:5173"

    # Storage. Either DATABASE_URL, or the DB_* parts below (how Key Vault / Container Apps supply them).
    database_url: str = f"sqlite:///{BACKEND_DIR / 'data' / 'legacynexus.db'}"
    db_host: str | None = None  # e.g. lnx-prod-pg.postgres.database.azure.com
    db_port: int = 5432
    db_name: str = "legacynexus"
    db_user: str | None = None
    db_password: str | None = None
    db_sslmode: str = "require"  # Azure PostgreSQL requires TLS
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_seconds: int = 1800
    storage_dir: Path = BACKEND_DIR / "data" / "storage"
    sample_data_dir: Path = BACKEND_DIR / "sample_data"

    # Upload limits. 0 = unlimited (the default). Uploads stream to disk in chunks, so size is bounded only
    # by free disk space; set a value to protect a shared server.
    max_upload_mb: int = Field(0, ge=0)  # size of one upload request (e.g. a zipped repository)
    max_extracted_mb: int = Field(0, ge=0)  # total size of the source files kept from one archive
    max_files_per_project: int = Field(0, ge=0)  # source files kept from one archive
    max_files_per_request: int = Field(0, ge=0)  # files in one upload request (whole-folder uploads)
    max_source_file_mb: int = Field(0, ge=0)  # files larger than this are skipped

    # Accounts
    allow_signup: bool = True  # anyone can create an account (role: analyst); set false for invite-only
    signup_default_role: str = "analyst"
    password_reset_minutes: int = 30  # lifetime of a password reset link
    app_base_url: str = "http://localhost:5173"  # used to build links in emails

    # Outgoing email (password reset). When SMTP_HOST is empty, development returns the link on screen
    # and every environment writes it to the server log.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "LegacyNexus <no-reply@legacynexus.local>"
    smtp_starttls: bool = True

    # First-run administrator. Created only when the user table is empty.
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_name: str = "Administrator"

    # Q&A defaults (editable at runtime from Settings > AI model)
    llm_request_timeout_seconds: int = 120  # headroom for slower/reasoning models (e.g. Gemini 3.x)
    qa_top_k: int = 8
    qa_context_char_budget: int = 14_000
    qa_min_retrieval_score: float = 1.5

    # Optional: AI model and embedding model supplied by the environment (e.g. Azure OpenAI with the key from
    # Key Vault). Applied at start-up ONLY while nothing is configured in Settings, so an administrator's choice
    # is never overwritten. Providers: azure_openai | openai | anthropic | gemini | ollama | openai_compatible.
    ai_provider: str | None = None
    ai_model: str = ""
    ai_deployment: str = ""
    ai_base_url: str = ""
    ai_api_version: str = ""
    ai_api_key: str | None = None
    embedding_provider: str | None = None  # azure_openai | openai | ollama | openai_compatible
    embedding_model: str = ""
    embedding_deployment: str = ""
    embedding_base_url: str = ""
    embedding_api_version: str = ""
    embedding_api_key: str | None = None  # empty = reuse AI_API_KEY when the provider is the same

    # Background jobs (analysis, semantic indexing).
    #   inline     = run inside the API process (development, single-container installs)
    #   servicebus = send to an Azure Service Bus queue, processed by `python -m app.worker` containers
    job_backend: str = "inline"
    servicebus_queue: str = "analysis-jobs"
    servicebus_namespace: str | None = None  # e.g. lnx-prod.servicebus.windows.net (uses managed identity)
    servicebus_connection_string: str | None = None  # alternative to the namespace, e.g. for local testing

    # Semantic search. auto = pgvector when the database is PostgreSQL with the extension, else a file store.
    vector_store: str = "auto"  # auto | pgvector | file
    embedding_request_timeout_seconds: int = 120

    # Document Q&A (role-based Q&A over a single uploaded PDF/DOCX/TXT/Markdown document).
    docqa_max_file_mb: int = Field(0, ge=0)  # largest document accepted for upload; 0 = unlimited
    docqa_chunk_chars: int = 1200  # target characters per chunk (split on paragraph/sentence boundaries)
    docqa_chunk_overlap: int = 150  # characters repeated between consecutive chunks to preserve context
    docqa_max_chunks: int = Field(0, ge=0)  # cap per document (larger ones are truncated); 0 = unlimited
    docqa_top_k: int = 6  # excerpts retrieved per question
    docqa_context_char_budget: int = 12_000  # maximum characters of excerpts sent to the model
    docqa_min_confidence: float = 0.3  # below this the answer becomes an insufficient-information response

    # Document Q&A has its OWN database and vector store, separate from Code Q&A (the main database).
    #   DOCQA_DATABASE_URL  explicit connection string, or
    #   DOCQA_DB_NAME (+ optional DOCQA_DB_HOST/USER/PASSWORD) a second database, by default on the same server
    #   neither             SQLite: docqa.db next to the main database file; PostgreSQL: database DOCQA_DB_NAME
    docqa_database_url: str | None = None
    docqa_db_name: str = "legacynexus_docqa"
    docqa_db_host: str | None = None  # defaults to DB_HOST
    docqa_db_user: str | None = None  # defaults to DB_USER
    docqa_db_password: str | None = None  # defaults to DB_PASSWORD
    docqa_vector_store: str = "auto"  # auto | pgvector | file  (vectors live in the Document Q&A database)

    # Conversations (both Code Q&A and Document Q&A keep their own history).
    #   auto   = earlier turns are used only when a question looks like a follow-up ("what about X?", "why?",
    #            "who calls it?"), so stand-alone questions cost no extra tokens and still hit the answer cache
    #   always = earlier turns are always sent to the model;  off = every question stands alone
    conversation_context: str = "auto"
    conversation_turns: int = Field(4, ge=0, le=20)  # earlier exchanges considered
    conversation_context_chars: int = Field(2500, ge=0)  # cap on the history text sent to the model

    @field_validator("storage_dir", "sample_data_dir", mode="after")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value if value.is_absolute() else (BACKEND_DIR / value).resolve()

    @field_validator("database_url", "docqa_database_url", mode="after")
    @classmethod
    def _absolute_sqlite(cls, value: str | None) -> str | None:
        """Relative SQLite paths are resolved against the backend folder, whatever the working directory."""
        prefix = "sqlite:///"
        if value and value.startswith(prefix) and value != "sqlite:///:memory:":
            path = Path(value[len(prefix):])
            if not path.is_absolute():
                return prefix + (BACKEND_DIR / path).resolve().as_posix()
        return value

    @model_validator(mode="after")
    def _database_from_parts(self) -> "Settings":
        """Build DATABASE_URL from DB_HOST/DB_USER/DB_PASSWORD when those are given (cloud secrets)."""
        if self.db_host and self.db_user:
            from urllib.parse import quote_plus

            pwd = quote_plus(self.db_password or "")
            self.database_url = (f"postgresql+psycopg://{quote_plus(self.db_user)}:{pwd}@{self.db_host}:{self.db_port}/"
                                 f"{self.db_name}?sslmode={self.db_sslmode}")
        return self

    @model_validator(mode="after")
    def _docqa_database(self) -> "Settings":
        """Work out the Document Q&A database when DOCQA_DATABASE_URL is not given (see the settings above)."""
        if self.docqa_database_url:
            return self
        host, user = self.docqa_db_host or self.db_host, self.docqa_db_user or self.db_user
        if host and user:
            from urllib.parse import quote_plus

            pwd = quote_plus(self.docqa_db_password or self.db_password or "")
            self.docqa_database_url = (f"postgresql+psycopg://{quote_plus(user)}:{pwd}@{host}:{self.db_port}/"
                                       f"{self.docqa_db_name}?sslmode={self.db_sslmode}")
        elif self.is_sqlite:
            main = self.database_url[len("sqlite:///"):]
            self.docqa_database_url = ("sqlite:///:memory:" if main in ("", ":memory:")
                                       else "sqlite:///" + (Path(main).parent / "docqa.db").as_posix())
        else:  # PostgreSQL given as one URL: same server, separate database
            from sqlalchemy.engine import make_url

            self.docqa_database_url = make_url(self.database_url).set(database=self.docqa_db_name) \
                .render_as_string(hide_password=False)
        return self

    @model_validator(mode="after")
    def _check_profile(self) -> "Settings":
        profile = self.deployment_profile.lower()
        if profile not in {"local", "cloud"}:
            raise ValueError("DEPLOYMENT_PROFILE must be 'local' or 'cloud'")
        if profile == "cloud" and self.database_url.startswith("sqlite"):
            raise ValueError("The cloud profile needs PostgreSQL: set DATABASE_URL or DB_HOST/DB_USER/DB_PASSWORD")
        if profile == "cloud" and (self.docqa_database_url or "").startswith("sqlite"):
            raise ValueError("The cloud profile needs PostgreSQL for Document Q&A: set DOCQA_DATABASE_URL or DOCQA_DB_NAME")
        if self.docqa_database_url == self.database_url and self.database_url != "sqlite:///:memory:":
            raise ValueError("Document Q&A must use a separate database: DOCQA_DATABASE_URL equals DATABASE_URL")
        if self.conversation_context.lower() not in {"auto", "always", "off"}:
            raise ValueError("CONVERSATION_CONTEXT must be 'auto', 'always' or 'off'")
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def docqa_is_sqlite(self) -> bool:
        return (self.docqa_database_url or "").startswith("sqlite")

    @model_validator(mode="after")
    def _refuse_demo_secrets_outside_development(self) -> "Settings":
        """Outside development, refuse to start with the sample secret key or the demo admin password.

        In Azure these values come from Key Vault (Container Apps secret references), never from a file.
        """
        if self.environment.lower() in {"development", "dev", "test"}:
            return self
        problems = []
        if self.secret_key.startswith(DEMO_SECRET_PREFIX):
            problems.append("SECRET_KEY is the sample value")
        if self.bootstrap_admin_password and self.bootstrap_admin_password in DEMO_ADMIN_PASSWORDS:
            problems.append("BOOTSTRAP_ADMIN_PASSWORD is the demo password")
        if problems:
            raise ValueError(f"Unsafe configuration for environment '{self.environment}': " + "; ".join(problems))
        return self

    @staticmethod
    def mb_limit(megabytes: int) -> int | None:
        """Bytes for a megabyte setting, or None when the setting is 0 (unlimited)."""
        return megabytes * 1024 * 1024 if megabytes else None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
