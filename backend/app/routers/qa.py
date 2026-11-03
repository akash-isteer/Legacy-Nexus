from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select

from app.config import get_settings
from app.deps import DB, CurrentUser, ReadyProject
from app.engine.graph_answers import GraphAnswerer
from app.engine.qa import suggested_questions
from app.llm import gateway
from app.models import QAConversation, QAEntry
from app.schemas import AskRequest
from app.services import ai_config, analysis_runner, artifacts, audit, conversation, jobs, semantic
from app.services.conversation_store import ConversationNotFound, ConversationStore

router = APIRouter(prefix="/projects/{project_id}", tags=["qa"])

# Code Q&A conversations live in the main database, next to the project they are about.
conversations = ConversationStore(QAConversation, QAEntry, "project_id")


def _entry_view(e: QAEntry) -> dict:
    return {"id": e.id, "created_at": e.created_at, "conversation_id": e.conversation_id, **e.response}


@router.post("/ask")
def ask(body: AskRequest, project: ReadyProject, user: CurrentUser, db: DB, request: Request) -> dict:
    version = project.analyzed_at.isoformat() if project.analyzed_at else "0"
    engine = artifacts.qa_engine(db, project.id, version)
    if engine is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The code index is missing; run the analysis again")
    question = body.question.strip()
    try:
        conv = (conversations.create(db, project.id, user.id) if body.new_conversation
                else conversations.resolve(db, project.id, user.id, body.conversation_id))
    except ConversationNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    s = get_settings()
    ctx = conversation.plan(question, conversations.recent_turns(db, conv.id, s.conversation_turns),
                            s.conversation_context, s.conversation_turns, s.conversation_context_chars)

    # 1. Factual questions are answered exactly from the knowledge graph: no AI call, no cost.
    #    A follow-up such as "who calls it?" is tried again in its stand-alone form ("who calls ACCOUNT?").
    d = artifacts.load_many(db, project.id, ["graph", "lineage", "insights", "rules"])
    result = None
    if "graph" in d and "lineage" in d:
        answerer = GraphAnswerer(d["graph"], d["lineage"], d.get("insights"), d.get("rules"))
        result = answerer.answer(question)
        if result is None and ctx.resolved_question:
            result = answerer.answer(ctx.resolved_question)
            if result is not None:
                result["question"] = question
    if result is None:
        # 2. Otherwise: retrieval, then the model through the gateway (scrub, cache, budget, route, failover, log).
        row = ai_config.get_row(db)
        llm = ai_config.to_llm_config(row)
        searcher, _ = semantic.searcher_for(db, project)

        def complete(cfg, system, prompt, sources):
            return gateway.complete(db, cfg, system, prompt, purpose="ask", tier="large", project_id=project.id,
                                    user_id=user.id, sources=sources)

        result = engine.answer(question, ai_config.to_qa_settings(row), llm if llm.configured else None,
                               semantic=searcher, complete=complete, history=ctx.history_text,
                               retrieval_query=ctx.retrieval_query, resolved_question=ctx.resolved_question)
    result["context"] = ctx.public()
    entry = QAEntry(project_id=project.id, conversation_id=conv.id, user_id=user.id, question=question,
                    response=result, confidence=result.get("confidence", 0.0),
                    not_found=result.get("not_found", False))
    db.add(entry)
    conversations.touch(conv, question)
    db.commit()
    audit.record(db, "question_asked", user.id, f"project:{project.id}",
                 {"mode": result.get("mode"), "confidence": result.get("confidence"),
                  "tokens": result.get("usage"), "conversation": conv.id, "follow_up": ctx.follow_up}, request)
    return {**_entry_view(entry), "conversation_title": conv.title}


@router.get("/qa/history")
def history(project: ReadyProject, user: CurrentUser, db: DB, limit: int = 30,
            conversation_id: int | None = None) -> list[dict]:
    """The user's recent questions on this project, optionally for one conversation."""
    q = select(QAEntry).where(QAEntry.project_id == project.id, QAEntry.user_id == user.id)
    if conversation_id is not None:
        q = q.where(QAEntry.conversation_id == conversation_id)
    rows = db.scalars(q.order_by(QAEntry.created_at.desc(), QAEntry.id.desc()).limit(min(limit, 100))).all()
    return [_entry_view(r) for r in reversed(rows)]


@router.get("/conversations")
def list_conversations(project: ReadyProject, user: CurrentUser, db: DB) -> list[dict]:
    return conversations.list(db, project.id, user.id)


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def new_conversation(project: ReadyProject, user: CurrentUser, db: DB) -> dict:
    c = conversations.create(db, project.id, user.id)
    db.commit()
    return conversations.public(c)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, project: ReadyProject, user: CurrentUser, db: DB) -> dict:
    try:
        c = conversations.get(db, project.id, user.id, conversation_id)
    except ConversationNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    entries = conversations.entries(db, c.id)
    return {**conversations.public(c, len(entries)), "entries": [_entry_view(e) for e in entries]}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, project: ReadyProject, user: CurrentUser, db: DB) -> Response:
    try:
        c = conversations.get(db, project.id, user.id, conversation_id)
    except ConversationNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    conversations.delete(db, c)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/qa/suggestions")
def suggestions(project: ReadyProject, db: DB) -> list[str]:
    d = artifacts.load_many(db, project.id, ["insights", "graph", "rules"])
    if len(d) < 3:
        return []
    return suggested_questions(d["insights"], d["graph"], d["rules"])


@router.get("/semantic-index")
def semantic_index(project: ReadyProject, db: DB) -> dict:
    return semantic.index_state(db, project)


@router.post("/semantic-index", status_code=status.HTTP_202_ACCEPTED)
def build_semantic_index(project: ReadyProject, user: CurrentUser, db: DB, background: BackgroundTasks,
                         request: Request) -> dict:
    if semantic.active_config(db) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enable semantic search and choose an embedding model in Settings first")
    if semantic.is_building(project.id) or analysis_runner.is_busy(project):
        raise HTTPException(status.HTTP_409_CONFLICT, "Indexing or analysis is already running for this project")
    try:
        jobs.submit(jobs.SEMANTIC_INDEX, project.id, background)
    except jobs.JobSubmitError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    audit.record(db, "semantic_index_started", user.id, f"project:{project.id}", request=request)
    state = semantic.index_state(db, project)
    return {**state, "building": True}
