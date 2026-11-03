"""Remove everything that belongs to a project.

SQLite does not enforce ON DELETE CASCADE unless foreign keys are switched on per connection, and it reuses the
id of a deleted project. Without this, a new project could inherit the files, graph, rules and Q&A history of a
deleted one. The rules come from the models' own foreign keys, so a new per-project table is covered automatically.
"""
from __future__ import annotations

from sqlalchemy import Table, delete, update
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.database import Base


def _project_links() -> list[tuple[Table, str, str]]:
    """(table, column, rule) for every column that points at projects.id; rule is "CASCADE" or "SET NULL"."""
    from app import models  # noqa: F401  (registers tables)

    links = []
    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table.name == "projects" and fk.column.name == "id":
                links.append((table, fk.parent.name, (fk.ondelete or "CASCADE").upper()))
    return links


def purge_project_rows(db: Session | Connection, project_id: int) -> None:
    """Delete (or detach) all rows of one project. Call before deleting the project row itself."""
    for table, col, rule in reversed(_project_links()):
        c = table.c[col]
        if rule == "SET NULL":
            db.execute(update(table).where(c == project_id).values({col: None}))
        else:
            db.execute(delete(table).where(c == project_id))


def purge_orphans(db: Session | Connection) -> dict[str, int]:
    """Clean up rows left behind by projects deleted before this fix. Returns rows touched per table."""
    from sqlalchemy import inspect, select

    from app.models import Project

    bind = db.get_bind() if isinstance(db, Session) else db
    existing = set(inspect(bind).get_table_names())  # run from an older migration: newer tables may not exist yet
    live = select(Project.id)
    touched = {}
    for table, col, rule in reversed(_project_links()):
        if table.name not in existing:
            continue
        c = table.c[col]
        stale = c.is_not(None) & c.not_in(live)
        if rule == "SET NULL":
            res = db.execute(update(table).where(stale).values({col: None}))
        else:
            res = db.execute(delete(table).where(stale))
        if res.rowcount:
            touched[table.name] = res.rowcount
    return touched
