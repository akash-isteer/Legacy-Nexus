"""Alembic environment: uses the application's engine and models, so there is one source of DB settings."""
from alembic import context

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.database import Base, engine
from app.engine.vectors import PgVectorStore

config = context.config
target_metadata = Base.metadata

# Tables created and managed at runtime rather than by migrations.
UNMANAGED_TABLES = {PgVectorStore.TABLE}


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and name in UNMANAGED_TABLES)


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:  # called from app.database.init_db with an open connection
        _run(connection)
        return
    with engine.connect() as conn:
        _run(conn)
        conn.commit()


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        render_as_batch=connection.dialect.name == "sqlite",  # SQLite needs table rebuilds for ALTERs
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline SQL generation is not supported; run migrations against a database.")
run_migrations_online()
