"""Database engines and session management.

Two independent databases:
  * the main database: users, projects, the code knowledge graph and Code Q&A (conversations, vectors)
  * the Document Q&A database: uploaded documents, their conversations and their vectors

Each is SQLite locally by default and PostgreSQL (Azure Database for PostgreSQL) in the cloud, chosen only by
configuration (DATABASE_URL / DB_*, DOCQA_DATABASE_URL / DOCQA_DB_*).
"""
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()


def _make_engine(url: str) -> Engine:
    connect_args: dict = {}
    pool: dict = {}
    if url.startswith("sqlite"):  # local profile: a single file next to the backend
        connect_args = {"check_same_thread": False}
        db_path = url.replace("sqlite:///", "", 1)
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    else:  # PostgreSQL (local container or Azure Flexible Server): pooled connections
        pool = {"pool_size": settings.db_pool_size, "max_overflow": settings.db_max_overflow,
                "pool_recycle": settings.db_pool_recycle_seconds}
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True, **pool)


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

docqa_engine = _make_engine(settings.docqa_database_url)
DocSessionLocal = sessionmaker(bind=docqa_engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Tables of the main database."""


class DocBase(DeclarativeBase):
    """Tables of the Document Q&A database (never joined with the main database)."""


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_docqa_db() -> Iterator[Session]:
    db = DocSessionLocal()
    try:
        yield db
    finally:
        db.close()


BACKEND_DIR = Path(__file__).resolve().parent.parent
BASELINE_REVISION = "0001"  # schema the app created with create_all() before migrations existed
DOCQA_VERSION_TABLE = "alembic_version_docqa"


def _alembic(script_dir: str):
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / script_dir))
    return cfg


def init_db(bind=None) -> None:
    """Bring the main database schema up to date with Alembic migrations.

    A database created by an older version (tables present, no alembic_version) already has the baseline
    schema, so it is stamped at the baseline and then upgraded like any other.
    """
    from alembic import command
    from sqlalchemy import inspect

    from app import models  # noqa: F401  (registers tables)

    cfg = _alembic("migrations")
    with (bind or engine).begin() as conn:
        cfg.attributes["connection"] = conn
        tables = set(inspect(conn).get_table_names())
        if "users" in tables and "alembic_version" not in tables:
            command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")


def ensure_database(url: str) -> bool:
    """Create a PostgreSQL database that does not exist yet (e.g. an existing Docker volume, or a server where
    only the main database was created). Needs the CREATEDB privilege, which the Azure admin login has.
    Returns True when it was created. SQLite files are created on first connection, so nothing to do there."""
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import OperationalError

    if url.startswith("sqlite"):
        return False
    probe = create_engine(url, pool_pre_ping=True)
    try:
        with probe.connect():
            return False
    except OperationalError as exc:
        if "does not exist" not in str(exc):
            raise
    finally:
        probe.dispose()
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        admin.dispose()
    import logging

    logging.getLogger("legacynexus").info("Created database %s", target.database)
    return True


def init_docqa_db(bind=None) -> None:
    """Bring the Document Q&A database schema up to date (its own migration history)."""
    from alembic import command

    from app import models_docqa  # noqa: F401  (registers tables)

    if bind is None:
        ensure_database(settings.docqa_database_url)
    cfg = _alembic("migrations_docqa")
    with (bind or docqa_engine).begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def init_all() -> None:
    """Document Q&A first: the main migration that moves old document rows across needs it in place."""
    init_docqa_db()
    init_db()
