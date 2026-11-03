"""Document-based role-specific Q&A endpoints.

Standalone feature: a user uploads one document (PDF/DOCX/TXT/Markdown) and asks questions about it
from four perspectives — End User, Tester, Developer, Business Person. Documents are isolated per
user: a user can only see and query their own uploads.

Data isolation: documents, conversations, question history and vectors are stored in the Document Q&A
database and vector store (``DocDB``), never in the main database used by Code Q&A. The main database
(``DB``) is read only for sign-in and the shared AI/embedding settings, and written only by the audit log
and the LLM gateway's cost log.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_docqa_db
from app.deps import DB, CurrentUser
from app.models_docqa import DocQADocument, DocQAEntry
from app.schemas import DocAskRequest, DocCompareRequest
from app.services import audit, conversation, doc_extract, doc_qa, role_prompts
from app.services.conversation_store import ConversationNotFound
from app.services.doc_qa import conversations

router = APIRouter(prefix="/doc-qa", tags=["doc-qa"])

DocDB = Annotated[Session, Depends(get_docqa_db)]


def _get_document(document_id: int, ddb: Session, user) -> DocQADocument:
    doc = ddb.get(DocQADocument, document_id)
    if doc is None or doc.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


def _get_conversation(ddb: Session, doc: DocQADocument, user, conversation_id: int):
    try:
        return conversations.get(ddb, doc.id, user.id, conversation_id)
    except ConversationNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")


def _entry_view(e: DocQAEntry) -> dict:
    return {"id": e.id, "created_at": e.created_at, "conversation_id": e.conversation_id, **e.response}


@router.get("/roles")
def roles() -> list[dict]:
    """The four answer perspectives for the dropdown, in display order."""
    return [{"id": rid, "label": label} for rid, label in role_prompts.ROLES.items()]


@router.get("/documents")
def list_documents(user: CurrentUser, ddb: DocDB) -> list[dict]:
    rows = ddb.scalars(select(DocQADocument).where(DocQADocument.owner_id == user.id)
                       .order_by(DocQADocument.created_at.desc(), DocQADocument.id.desc())).all()
    return [doc_qa.public(d) for d in rows]


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(user: CurrentUser, db: DB, ddb: DocDB, request: Request,
                          file: UploadFile = File(...)) -> dict:
    settings = get_settings()
    limit = settings.mb_limit(settings.docqa_max_file_mb)
    raw = await file.read()
    if limit is not None and len(raw) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"The document is larger than the {settings.docqa_max_file_mb} MB limit.")
    if doc_extract.format_for(file.filename or "") is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Unsupported file type. Upload a PDF, Word (.docx), text (.txt) or Markdown (.md) file.")
    try:
        doc = doc_qa.ingest(db, ddb, user, file.filename or "document", raw)
    except doc_extract.ExtractionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    audit.record(db, "docqa_upload", user.id, f"docqa:{doc.id}",
                 {"format": doc.format, "chunks": doc.chunk_count, "embedded": doc.embedded}, request)
    return doc_qa.public(doc)


@router.get("/documents/{document_id}")
def get_document(document_id: int, ddb: DocDB, user: CurrentUser) -> dict:
    return doc_qa.public(_get_document(document_id, ddb, user))


@router.delete("/documents/{document_id}")
def delete_document(document_id: int, db: DB, ddb: DocDB, user: CurrentUser, request: Request) -> Response:
    doc_qa.delete_document(ddb, _get_document(document_id, ddb, user))
    audit.record(db, "docqa_delete", user.id, f"docqa:{document_id}", request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/documents/{document_id}/ask")
def ask(document_id: int, body: DocAskRequest, db: DB, ddb: DocDB, user: CurrentUser, request: Request) -> dict:
    doc = _get_document(document_id, ddb, user)
    question = body.question.strip()
    if body.new_conversation:
        conv = conversations.create(ddb, doc.id, user.id)
    elif body.conversation_id is not None:
        conv = _get_conversation(ddb, doc, user, body.conversation_id)
    else:
        conv = conversations.resolve(ddb, doc.id, user.id, None)
    s = get_settings()
    ctx = conversation.plan(question, conversations.recent_turns(ddb, conv.id, s.conversation_turns),
                            s.conversation_context, s.conversation_turns, s.conversation_context_chars)
    result = doc_qa.answer_for_role(db, doc, question, body.role, ddb, history=ctx.history_text,
                                    retrieval_query=ctx.retrieval_query, resolved_question=ctx.resolved_question)
    result["context"] = ctx.public()
    entry = DocQAEntry(document_id=doc.id, conversation_id=conv.id, user_id=user.id, question=question,
                       role=body.role, response=result, confidence=result.get("confidence", 0.0),
                       not_found=result.get("not_found", False))
    ddb.add(entry)
    conversations.touch(conv, question)
    ddb.commit()
    audit.record(db, "docqa_ask", user.id, f"docqa:{doc.id}",
                 {"role": body.role, "mode": result.get("mode"), "confidence": result.get("confidence"),
                  "conversation": conv.id, "follow_up": ctx.follow_up}, request)
    return {**_entry_view(entry), "conversation_title": conv.title}


@router.post("/documents/{document_id}/compare")
def compare(document_id: int, body: DocCompareRequest, db: DB, ddb: DocDB, user: CurrentUser,
            request: Request) -> dict:
    """Answer the same question from several perspectives at once, over the same retrieved evidence."""
    doc = _get_document(document_id, ddb, user)
    question = body.question.strip()
    roles = body.roles or list(role_prompts.ROLES.keys())
    answers = [doc_qa.answer_for_role(db, doc, question, role, ddb) for role in roles]
    audit.record(db, "docqa_compare", user.id, f"docqa:{doc.id}", {"roles": roles}, request)
    return {"question": question, "answers": answers}


@router.get("/documents/{document_id}/history")
def history(document_id: int, ddb: DocDB, user: CurrentUser, limit: int = 30,
            conversation_id: int | None = None) -> list[dict]:
    doc = _get_document(document_id, ddb, user)
    q = select(DocQAEntry).where(DocQAEntry.document_id == doc.id, DocQAEntry.user_id == user.id)
    if conversation_id is not None:
        q = q.where(DocQAEntry.conversation_id == conversation_id)
    rows = ddb.scalars(q.order_by(DocQAEntry.created_at.desc(), DocQAEntry.id.desc()).limit(min(limit, 100))).all()
    return [_entry_view(r) for r in reversed(rows)]


@router.get("/documents/{document_id}/conversations")
def list_conversations(document_id: int, ddb: DocDB, user: CurrentUser) -> list[dict]:
    doc = _get_document(document_id, ddb, user)
    return conversations.list(ddb, doc.id, user.id)


@router.post("/documents/{document_id}/conversations", status_code=status.HTTP_201_CREATED)
def new_conversation(document_id: int, ddb: DocDB, user: CurrentUser) -> dict:
    doc = _get_document(document_id, ddb, user)
    c = conversations.create(ddb, doc.id, user.id)
    ddb.commit()
    return conversations.public(c)


@router.get("/documents/{document_id}/conversations/{conversation_id}")
def get_conversation(document_id: int, conversation_id: int, ddb: DocDB, user: CurrentUser) -> dict:
    doc = _get_document(document_id, ddb, user)
    c = _get_conversation(ddb, doc, user, conversation_id)
    entries = conversations.entries(ddb, c.id)
    return {**conversations.public(c, len(entries)), "entries": [_entry_view(e) for e in entries]}


@router.delete("/documents/{document_id}/conversations/{conversation_id}")
def delete_conversation(document_id: int, conversation_id: int, ddb: DocDB, user: CurrentUser) -> Response:
    doc = _get_document(document_id, ddb, user)
    conversations.delete(ddb, _get_conversation(ddb, doc, user, conversation_id))
    ddb.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
