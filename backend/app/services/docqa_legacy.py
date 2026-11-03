"""One-time move of Document Q&A data out of the main database into its own database.

Older versions kept uploaded documents and their question history in the main database. Main migration 0007
calls :func:`move_to_docqa_database` and then drops the old tables, so nothing about documents stays next to
the code data. Each (document, user) history becomes one conversation called "Earlier questions".
Per-chunk embeddings stored inline by older versions are moved to the document vector store the first time
the document is queried (see services.doc_qa).
"""
from __future__ import annotations

import logging

from sqlalchemy import func, inspect, insert, select, text
from sqlalchemy.engine import Connection

log = logging.getLogger("legacynexus.docqa")


def move_to_docqa_database(main: Connection) -> int:
    """Copy documents and entries from the main database connection. Returns the number of documents moved."""
    from app.database import docqa_engine
    from app.models_docqa import DocQAConversation, DocQADocument, DocQAEntry

    tables = set(inspect(main).get_table_names())
    if "docqa_documents" not in tables:
        return 0
    doc_t, conv_t, entry_t = DocQADocument.__table__, DocQAConversation.__table__, DocQAEntry.__table__
    docs = main.execute(select(doc_t)).mappings().all()
    if not docs:
        return 0
    old_entry_cols = [c for c in entry_t.c if c.name != "conversation_id"]  # the old table had no conversations
    entries = (main.execute(select(*old_entry_cols).select_from(entry_t).order_by(entry_t.c.id)).mappings().all()
               if "docqa_entries" in tables else [])

    with docqa_engine.begin() as dc:
        if "docqa_documents" not in set(inspect(dc).get_table_names()):
            raise RuntimeError("The Document Q&A database is not initialised; run init_docqa_db() first")
        keep_ids = dc.execute(select(func.count()).select_from(doc_t)).scalar() == 0
        doc_ids: dict[int, int] = {}
        for d in docs:
            row = dict(d)
            old_id = row.pop("id")
            if keep_ids:
                row["id"] = old_id
            doc_ids[old_id] = dc.execute(insert(doc_t).values(**row).returning(doc_t.c.id)).scalar_one()

        conversations: dict[tuple[int, int], int] = {}
        for e in entries:
            if e["document_id"] not in doc_ids:
                continue
            doc_id = doc_ids[e["document_id"]]
            key = (doc_id, e["user_id"])
            if key not in conversations:
                conversations[key] = dc.execute(insert(conv_t).values(
                    document_id=doc_id, user_id=e["user_id"], title="Earlier questions",
                    created_at=e["created_at"], updated_at=e["created_at"]).returning(conv_t.c.id)).scalar_one()
            row = {k: v for k, v in dict(e).items() if k != "id"}
            row.update(document_id=doc_id, conversation_id=conversations[key])
            dc.execute(insert(entry_t).values(**row))
            dc.execute(conv_t.update().where(conv_t.c.id == conversations[key]).values(updated_at=e["created_at"]))

        if keep_ids and dc.dialect.name == "postgresql":  # explicit ids do not advance the sequence
            dc.execute(text("SELECT setval(pg_get_serial_sequence('docqa_documents', 'id'), "
                            "(SELECT COALESCE(MAX(id), 1) FROM docqa_documents))"))
    log.info("Moved %s Document Q&A document(s) and %s question(s) to the Document Q&A database",
             len(doc_ids), len(entries))
    return len(doc_ids)
