"""Alembic environment for the Document Q&A database (separate from the main database and its migrations)."""
from alembic import context

from app import models_docqa  # noqa: F401  (registers every Document Q&A table on DocBase.metadata)
from app.database import DOCQA_VERSION_TABLE, DocBase, docqa_engine
from app.engine.vectors import DOC_TABLE

config = context.config
target_metadata = DocBase.metadata

UNMANAGED_TABLES = {DOC_TABLE}  # pgvector table created at runtime


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and name in UNMANAGED_TABLES)


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:  # called from app.database.init_docqa_db with an open connection
        _run(connection)
        return
    with docqa_engine.connect() as conn:
        _run(conn)
        conn.commit()


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        version_table=DOCQA_VERSION_TABLE,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline SQL generation is not supported; run migrations against a database.")
run_migrations_online()
