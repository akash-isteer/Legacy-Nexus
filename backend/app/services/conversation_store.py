"""Storage of conversations, shared by Code Q&A (main database) and Document Q&A (its own database).

Each module creates one ConversationStore over its own tables and passes a session for its own database, so
the two histories never touch. ``parent`` is the column that ties a conversation to what it is about:
``project_id`` for code, ``document_id`` for documents.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.services.conversation import Turn, title_for


class ConversationNotFound(LookupError):
    pass


class ConversationStore:
    def __init__(self, conversation_model, entry_model, parent: str):
        self.C, self.E, self.parent = conversation_model, entry_model, parent

    # ------------------------------------------------------------------ conversations
    def get(self, db: Session, parent_id: int, user_id: int, conversation_id: int):
        c = db.get(self.C, conversation_id)
        if c is None or getattr(c, self.parent) != parent_id or c.user_id != user_id:
            raise ConversationNotFound("Conversation not found")
        return c

    def latest(self, db: Session, parent_id: int, user_id: int):
        return db.scalars(select(self.C).where(getattr(self.C, self.parent) == parent_id, self.C.user_id == user_id)
                          .order_by(self.C.updated_at.desc(), self.C.id.desc()).limit(1)).first()

    def create(self, db: Session, parent_id: int, user_id: int, title: str = ""):
        now = datetime.now(timezone.utc)
        c = self.C(**{self.parent: parent_id}, user_id=user_id, title=title, created_at=now, updated_at=now)
        db.add(c)
        db.flush()
        return c

    def resolve(self, db: Session, parent_id: int, user_id: int, conversation_id: int | None):
        """The conversation a question belongs to: the one named, else the user's latest, else a new one."""
        if conversation_id is not None:
            return self.get(db, parent_id, user_id, conversation_id)
        return self.latest(db, parent_id, user_id) or self.create(db, parent_id, user_id)

    def list(self, db: Session, parent_id: int, user_id: int) -> list[dict]:
        counts = dict(db.execute(select(self.E.conversation_id, func.count(self.E.id))
                                 .where(getattr(self.E, self.parent) == parent_id, self.E.user_id == user_id)
                                 .group_by(self.E.conversation_id)).all())
        rows = db.scalars(select(self.C).where(getattr(self.C, self.parent) == parent_id, self.C.user_id == user_id)
                          .order_by(self.C.updated_at.desc(), self.C.id.desc())).all()
        return [self.public(c, counts.get(c.id, 0)) for c in rows]

    @staticmethod
    def public(c, turns: int = 0) -> dict:
        return {"id": c.id, "title": c.title or "New conversation", "turns": turns,
                "created_at": c.created_at, "updated_at": c.updated_at}

    def delete(self, db: Session, c) -> None:
        # Explicit, because SQLite does not enforce ON DELETE CASCADE.
        db.execute(delete(self.E).where(self.E.conversation_id == c.id))
        db.delete(c)

    def delete_for_parent(self, db: Session, parent_id: int) -> None:
        db.execute(delete(self.E).where(getattr(self.E, self.parent) == parent_id))
        db.execute(delete(self.C).where(getattr(self.C, self.parent) == parent_id))

    # ------------------------------------------------------------------ entries
    def entries(self, db: Session, conversation_id: int, limit: int = 100) -> list:
        rows = db.scalars(select(self.E).where(self.E.conversation_id == conversation_id)
                          .order_by(self.E.id.desc()).limit(limit)).all()
        return list(reversed(rows))

    def recent_turns(self, db: Session, conversation_id: int, n: int) -> list[Turn]:
        if n <= 0:
            return []
        return [to_turn(e) for e in self.entries(db, conversation_id, n)]

    def touch(self, c, question: str) -> None:
        c.updated_at = datetime.now(timezone.utc)
        if not c.title:
            c.title = title_for(question)


def _norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def to_turn(entry) -> Turn:
    """An earlier exchange as conversation context. Subjects named in the question come first."""
    r = entry.response or {}
    context = r.get("context") or {}
    asked = _norm(context.get("resolved_question") or entry.question)
    names = [e["name"] for e in r.get("entities") or [] if isinstance(e, dict) and e.get("name")]
    named = [n for n in names if _norm(n) and _norm(n) in asked]
    subjects = list(dict.fromkeys(named + [n for n in names if n not in named]))
    return Turn(question=entry.question, answer=str(r.get("answer", "")), subjects=subjects[:6],
                resolved=context.get("resolved_question"))
