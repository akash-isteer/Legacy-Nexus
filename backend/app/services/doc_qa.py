"""Document-based role-specific Q&A.

A self-contained retrieval-augmented pipeline over ONE uploaded document, independent of the legacy
code knowledge graph. It reuses the shared infrastructure — the LLM provider adapters
(:mod:`app.llm.providers`), the embedding adapters (:mod:`app.llm.embeddings`), the lexical index
(:class:`app.engine.retrieval.BM25Index`) and the organisation AI/embedding settings — but adds its
own text chunking, hybrid retrieval, role-specific prompting and citation grounding.

Flow: extract text -> chunk (keeping page/section metadata) -> optionally embed -> for each question:
retrieve the most relevant chunks (semantic + keyword, fused) -> build a role-specific prompt from the
retrieved excerpts -> ask the model for a strict JSON answer -> validate citations against the sent
excerpts -> calibrate confidence. When no model is configured it returns the retrieved excerpts as
evidence; when nothing relevant is found it returns an explicit insufficient-information answer.

Storage is separate from Code Q&A: documents, conversations and entries live in the Document Q&A database
(``ddb`` below) and chunk vectors in the "docs" vector store. The main database (``db``) is used only for
the shared organisation settings (AI and embedding model) and the LLM gateway's cache and usage log.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.config import get_settings
from app.engine.retrieval import BM25Index
from app.engine.vectors import get_store
from app.llm.embeddings import EmbeddingConfig, EmbeddingError, embed_texts
from app.llm.providers import LLMConfig, LLMError, complete_json
from app.models import User
from app.models_docqa import DocQAConversation, DocQADocument, DocQAEntry
from app.services import ai_config, doc_extract, role_prompts, semantic
from app.services.conversation_store import ConversationStore

VECTORS = "docs"  # the Document Q&A vector namespace (never shared with code vectors)
conversations = ConversationStore(DocQAConversation, DocQAEntry, "document_id")  # in the Document Q&A database

log = logging.getLogger("legacynexus.docqa")

RRF_K = 60  # Reciprocal Rank Fusion constant (standard value)
INSUFFICIENT = "The uploaded document does not contain sufficient information to answer this question."


@dataclass
class DocQASettings:
    top_k: int
    context_char_budget: int
    min_confidence: float

    @classmethod
    def from_config(cls) -> "DocQASettings":
        s = get_settings()
        return cls(top_k=s.docqa_top_k, context_char_budget=s.docqa_context_char_budget,
                   min_confidence=s.docqa_min_confidence)


# ------------------------------------------------------------------ chunking

def build_chunks(extraction: doc_extract.Extraction, chunk_chars: int, overlap: int,
                 max_chunks: int) -> tuple[list[dict], bool]:
    """Pack extracted blocks into chunks of roughly ``chunk_chars``, preserving page/section metadata.

    A new chunk is started whenever the PDF page changes, so every chunk maps to a single page and its
    citation is unambiguous. Within a page, a short tail of the previous chunk is repeated as overlap so
    that facts spanning a boundary are still retrievable.
    """
    chunks: list[dict] = []
    buf: list[doc_extract.Block] = []

    def buf_text() -> str:
        return "\n".join(b.text for b in buf).strip()

    def flush(seed_overlap: bool) -> None:
        nonlocal buf
        text = buf_text()
        if not text:
            buf = []
            return
        index = len(chunks)
        page = next((b.page for b in buf if b.page is not None), None)
        section = next((b.section for b in buf if b.section), None)
        chunks.append({
            "id": f"S{index + 1}", "index": index, "page": page, "section": section,
            "title": _title(page, section), "text": text, "symbols": [],
        })
        if seed_overlap and overlap > 0 and len(text) > overlap:
            tail = text[-overlap:]
            cut = tail.find(" ")
            tail = tail[cut + 1:] if cut != -1 else tail
            last = buf[-1]
            buf = [doc_extract.Block(text=tail, page=last.page, section=section)]
        else:
            buf = []

    for block in extraction.blocks:
        # Keep each chunk inside one PDF page.
        if buf and block.page is not None and buf[-1].page is not None and block.page != buf[-1].page:
            flush(seed_overlap=False)
        buf.append(block)
        if len(buf_text()) >= chunk_chars:
            flush(seed_overlap=True)
    flush(seed_overlap=False)

    if not max_chunks:  # 0 = unlimited
        return chunks, False
    truncated = len(chunks) > max_chunks
    return chunks[:max_chunks], truncated


def _title(page: int | None, section: str | None) -> str:
    parts = []
    if page is not None:
        parts.append(f"page {page}")
    if section:
        parts.append(f"“{section[:60]}”")
    return " · ".join(parts) if parts else "excerpt"


# ------------------------------------------------------------------ ingestion

def ingest(db: Session, ddb: Session, user: User, filename: str, raw: bytes) -> DocQADocument:
    """Extract, chunk and (if an embedding model is configured) embed a document, then persist it.

    ``db`` is the main database (embedding settings), ``ddb`` the Document Q&A database (the document).
    Raises :class:`doc_extract.ExtractionError` for unsupported/empty/unreadable documents.
    """
    s = get_settings()
    fmt = doc_extract.format_for(filename)
    if fmt is None:
        raise doc_extract.ExtractionError(
            "Unsupported file type. Upload a PDF, Word document (.docx), text (.txt) or Markdown (.md) file."
        )
    extraction = doc_extract.extract(filename, raw)
    chunks, truncated = build_chunks(extraction, s.docqa_chunk_chars, s.docqa_chunk_overlap, s.docqa_max_chunks)
    if not chunks:
        raise doc_extract.ExtractionError("No readable text could be extracted from this document.")

    vectors, model_key = None, ""
    cfg = semantic.active_config(db)
    if cfg is not None:
        try:
            vectors = embed_texts(cfg, [c["text"] for c in chunks], kind="document")
            model_key = cfg.key
        except EmbeddingError as exc:
            # Embeddings are optional; fall back to keyword retrieval but keep the document usable.
            log.warning("Embedding the document failed, keyword search only: %s", exc)

    doc = DocQADocument(
        owner_id=user.id, filename=filename[:512], format=fmt, size_bytes=len(raw), status="ready",
        char_count=extraction.char_count, chunk_count=len(chunks), page_count=extraction.page_count,
        truncated=truncated, embedded=False, model_key=model_key, chunks=chunks,
    )
    ddb.add(doc)
    ddb.commit()
    if vectors:
        try:
            get_store(VECTORS).save(doc.id, model_key, [c["id"] for c in chunks], vectors)
            doc.embedded = True
        except Exception as exc:  # vector store down: the document still works with keyword search
            log.warning("Saving document vectors failed, keyword search only: %s", exc)
        ddb.commit()
    ddb.refresh(doc)
    return doc


def delete_document(ddb: Session, doc: DocQADocument) -> None:
    """Remove a document, its conversations, its entries and its vectors."""
    try:
        get_store(VECTORS).delete(doc.id)
    except Exception:  # best effort: the rows are removed regardless
        log.warning("Could not delete vectors for document %s", doc.id)
    conversations.delete_for_parent(ddb, doc.id)
    ddb.delete(doc)
    ddb.commit()


def _move_inline_vectors(ddb: Session, doc: DocQADocument) -> None:
    """Older versions kept each chunk's embedding inside the document row; move them to the vector store once."""
    inline = [(c["id"], c["embedding"]) for c in doc.chunks if c.get("embedding")]
    if not inline:
        return
    get_store(VECTORS).save(doc.id, doc.model_key, [cid for cid, _ in inline], [v for _, v in inline])
    doc.chunks = [{k: v for k, v in c.items() if k != "embedding"} for c in doc.chunks]
    flag_modified(doc, "chunks")
    ddb.commit()


def public(doc: DocQADocument) -> dict:
    """Document summary for the UI (never includes the chunk text or embeddings)."""
    return {
        "id": doc.id, "filename": doc.filename, "format": doc.format, "size_bytes": doc.size_bytes,
        "status": doc.status, "error": doc.error, "char_count": doc.char_count, "chunk_count": doc.chunk_count,
        "page_count": doc.page_count, "truncated": doc.truncated, "embedded": doc.embedded,
        "search_mode": "hybrid" if doc.embedded else "keyword",
        "created_at": doc.created_at, "updated_at": doc.updated_at,
    }


# ------------------------------------------------------------------ retrieval

def _semantic_hits(ddb: Session | None, doc: DocQADocument, question: str, cfg: EmbeddingConfig, pool: int
                   ) -> list[tuple[str, float]]:
    if ddb is not None:
        _move_inline_vectors(ddb, doc)
    query = embed_texts(cfg, [question], kind="query")[0]
    return get_store(VECTORS).search(doc.id, cfg.key, query, pool)


@dataclass
class DocRetrieval:
    evidence: list[dict]  # selected chunks with source number, score, similarity, found_by
    keyword_top: float
    semantic_top: float | None
    strength: float
    mode: str  # "hybrid" | "keyword"
    warning: str | None = None


def retrieve(db: Session, doc: DocQADocument, question: str, settings: DocQASettings,
             ddb: Session | None = None) -> DocRetrieval:
    chunks = doc.chunks
    by_id = {c["id"]: c for c in chunks}
    pool = max(20, settings.top_k * 3)

    keyword_hits = BM25Index(chunks).search(question, top_k=pool)
    keyword_top = keyword_hits[0][1] if keyword_hits else 0.0

    semantic_hits: list[tuple[str, float]] = []
    semantic_top: float | None = None
    warning = None
    min_sem = 0.35
    weight = 1.0
    cfg = semantic.active_config(db)
    use_semantic = bool(doc.embedded and cfg is not None and cfg.key == doc.model_key)
    if use_semantic:
        row = semantic.get_row(db)
        min_sem, weight = row.min_semantic_score, row.semantic_weight
        try:
            raw = _semantic_hits(ddb, doc, question, cfg, pool)
            semantic_top = raw[0][1] if raw else 0.0
            semantic_hits = [(cid, sim) for cid, sim in raw if sim >= min_sem]
        except Exception as exc:  # embedding service or vector store down: keyword search still answers
            warning = f"Semantic search was unavailable, used keyword search only. {exc}"
            use_semantic = False

    fused: dict[str, dict] = {}
    for rank, (chunk, score) in enumerate(keyword_hits):
        fused[chunk["id"]] = {"rrf": 1.0 / (RRF_K + rank + 1), "keyword_score": round(score, 2), "similarity": None}
    for rank, (cid, sim) in enumerate(semantic_hits):
        if cid not in by_id:
            continue
        entry = fused.setdefault(cid, {"rrf": 0.0, "keyword_score": None, "similarity": None})
        entry["rrf"] += weight / (RRF_K + rank + 1)
        entry["similarity"] = round(sim, 3)

    ranked = sorted(fused.items(), key=lambda kv: -kv[1]["rrf"])
    evidence, used = [], 0
    for cid, info in ranked:
        if len(evidence) >= settings.top_k:
            break
        chunk = by_id[cid]
        text = chunk["text"]
        if used + len(text) > settings.context_char_budget:
            remaining = settings.context_char_budget - used
            if remaining < 400:
                break
            text = text[:remaining].rsplit(" ", 1)[0]
        found_by = ("both" if info["keyword_score"] is not None and info["similarity"] is not None
                    else "keyword" if info["keyword_score"] is not None else "semantic")
        evidence.append({
            "source": len(evidence) + 1, "id": cid, "page": chunk["page"], "section": chunk["section"],
            "title": chunk["title"], "text": text, "score": info["keyword_score"],
            "similarity": info["similarity"], "found_by": found_by,
        })
        used += len(text)

    strength = _keyword_strength(keyword_top)
    if use_semantic and semantic_top is not None:
        strength = max(strength, _semantic_strength(semantic_top, min_sem))
    return DocRetrieval(evidence=evidence, keyword_top=keyword_top, semantic_top=semantic_top,
                        strength=round(strength, 3), mode="hybrid" if use_semantic else "keyword", warning=warning)


def _keyword_strength(top_score: float) -> float:
    return min(1.0, top_score / (top_score + 5.0) * 1.7)


def _semantic_strength(similarity: float, min_score: float) -> float:
    if min_score <= 0:
        return max(0.0, min(1.0, similarity))
    if similarity < min_score:
        return max(0.0, 0.5 * similarity / min_score)
    return min(1.0, 0.5 + 0.5 * (similarity - min_score) / 0.3)


# ------------------------------------------------------------------ answering

def answer(db: Session, doc: DocQADocument, question: str, role: str, llm: LLMConfig | None,
           settings: DocQASettings, ddb: Session | None = None, history: str = "",
           retrieval_query: str | None = None, resolved_question: str | None = None) -> dict:
    """Answer one question. For a follow-up, ``retrieval_query`` is what to search with, ``resolved_question``
    its stand-alone reading and ``history`` the earlier turns (see services.conversation)."""
    if not role_prompts.is_valid_role(role):
        role = role_prompts.DEFAULT_ROLE
    r = retrieve(db, doc, retrieval_query or question, settings, ddb)
    base = {
        "question": question,
        "role": role,
        "role_label": role_prompts.label(role),
        "evidence": [_evidence_view(e) for e in r.evidence],
        "retrieval": {"mode": r.mode, "chunks_sent": len(r.evidence),
                      "chars_sent": sum(len(e["text"]) for e in r.evidence),
                      "top_score": round(r.keyword_top, 2),
                      "top_similarity": None if r.semantic_top is None else round(r.semantic_top, 3),
                      "strength": r.strength,
                      "found_by": {k: sum(1 for e in r.evidence if e["found_by"] == k)
                                   for k in ("keyword", "semantic", "both")}},
    }
    if r.warning:
        base["warning"] = r.warning

    if not r.evidence:
        return {**base, **_abstain("No part of the document matched this question.")}
    if llm is None or not llm.configured:
        return {**base, **_evidence_only(r.evidence, r.strength)}

    prompt = _prompt(question, r.evidence, history, resolved_question)
    try:
        from app.llm import gateway  # scrub, cache, budget, routing, failover and usage log

        result = gateway.complete(db, llm, role_prompts.system_for(role), prompt, purpose="doc_qa", tier="large",
                                  user_id=doc.owner_id, call=complete_json,
                                  sources=[{"document": doc.filename, "page": e.get("page"), "section": e.get("section")}
                                           for e in r.evidence])
    except LLMError as exc:
        out = _evidence_only(r.evidence, r.strength)
        out["warning"] = f"The AI model call failed, showing the retrieved excerpts instead. {exc}"
        return {**base, **out}

    from app.engine.qa import parse_json  # reuse the shared tolerant JSON extractor
    parsed = parse_json(result.text)
    if parsed is None:
        return {**base, **_abstain("The model did not return valid structured output."), "model": result.model}

    validated = _validate(parsed, r.evidence)
    confidence = _confidence(parsed, validated, r.strength)
    not_found = (bool(parsed.get("not_found")) or validated["citations_valid"] == 0
                 or confidence["score"] < settings.min_confidence)
    answer_text = INSUFFICIENT if not_found else str(parsed.get("answer", "")).strip() or INSUFFICIENT
    reason = None
    if not_found and not parsed.get("not_found"):
        reason = ("No statement could be tied back to the document." if validated["citations_valid"] == 0
                  else "Confidence was below the threshold for a grounded answer.")
    missing = parsed.get("missing")
    return {
        **base,
        "mode": "llm",
        "answer": answer_text,
        "not_found": not_found,
        "abstain_reason": reason,
        "missing": str(missing).strip() if missing and not not_found else None,
        "confidence": round(confidence["score"] if not not_found else min(confidence["score"], 0.3), 2),
        "confidence_label": _label(confidence["score"]) if not not_found else "Low",
        "confidence_breakdown": confidence["parts"],
        "citations": [] if not_found else validated["citations"],
        "validation": {k: v for k, v in validated.items() if k != "citations"},
        "model": result.model,
        "usage": {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                  "latency_ms": result.latency_ms},
    }


def _prompt(question: str, evidence: list[dict], history: str = "", resolved_question: str | None = None) -> str:
    parts = [history] if history else []
    parts.append(f"QUESTION:\n{question}\n")
    if resolved_question and resolved_question != question:
        parts.append(f"(In this conversation the question means: {resolved_question})\n")
    parts.append("SOURCE EXCERPTS (cite each one you use by its [S#] label; earlier conversation is not a source):")
    for e in evidence:
        loc = _title(e["page"], e["section"])
        parts.append(f"[S{e['source']}] ({loc})\n{e['text']}\n")
    parts.append("Return the JSON object now.")
    return "\n".join(parts)


def _validate(parsed: dict, evidence: list[dict]) -> dict:
    by_source = {e["source"]: e for e in evidence}
    citations_out, total, valid = [], 0, 0
    for cite in parsed.get("citations") or []:
        if not isinstance(cite, dict):
            continue
        total += 1
        try:
            source = int(cite.get("source"))
        except (TypeError, ValueError):
            source = -1
        e = by_source.get(source)
        ok = e is not None
        valid += ok
        citations_out.append({
            "source": source, "valid": ok,
            "page": e["page"] if e else None, "section": e["section"] if e else None,
            "title": e["title"] if e else None, "note": str(cite.get("note", "") or "")[:300],
            "snippet": e["text"][:280] if e else None,
            "reason": None if ok else "Citation does not refer to a provided excerpt",
        })
    return {
        "citations": citations_out,
        "citations_total": total,
        "citations_valid": valid,
    }


def _confidence(parsed: dict, v: dict, retrieval: float) -> dict:
    try:
        model = max(0.0, min(1.0, float(parsed.get("confidence", 0.5))))
    except (TypeError, ValueError):
        model = 0.5
    citation = v["citations_valid"] / v["citations_total"] if v["citations_total"] else 0.0
    score = 0.35 * model + 0.30 * citation + 0.35 * retrieval
    parts = [
        {"name": "Model self-assessment", "weight": 0.35, "value": round(model, 2)},
        {"name": "Valid citations", "weight": 0.30, "value": round(citation, 2)},
        {"name": "Retrieval strength", "weight": 0.35, "value": round(retrieval, 2)},
    ]
    return {"score": round(score, 3), "parts": parts}


def _label(score: float) -> str:
    return "High" if score >= 0.75 else "Medium" if score >= 0.5 else "Low"


def _abstain(reason: str) -> dict:
    return {"mode": "abstained", "answer": INSUFFICIENT, "not_found": True, "abstain_reason": reason,
            "missing": None, "confidence": 0.0, "confidence_label": "Low", "confidence_breakdown": [],
            "citations": [], "validation": {}}


def _evidence_only(evidence: list[dict], retrieval: float) -> dict:
    citations = [{"source": e["source"], "valid": True, "page": e["page"], "section": e["section"],
                  "title": e["title"], "note": "", "snippet": e["text"][:280], "reason": None}
                 for e in evidence[:6]]
    score = round(0.6 * retrieval, 2)
    return {
        "mode": "evidence_only",
        "answer": ("No AI model is configured, so these are the most relevant passages from your document for this "
                   "question. Ask an administrator to configure a model in Settings for a written, role-specific answer."),
        "not_found": False,
        "abstain_reason": None,
        "missing": None,
        "confidence": score,
        "confidence_label": _label(score),
        "confidence_breakdown": [{"name": "Retrieval strength", "weight": 0.6, "value": round(retrieval, 2)}],
        "citations": citations,
        "validation": {"citations_total": len(citations), "citations_valid": len(citations)},
    }


def _evidence_view(e: dict) -> dict:
    return {"source": e["source"], "page": e["page"], "section": e["section"], "title": e["title"],
            "score": e["score"], "similarity": e.get("similarity"), "found_by": e.get("found_by", "keyword"),
            "snippet": e["text"][:600]}


# ------------------------------------------------------------------ orchestration helpers

def answer_for_role(db: Session, doc: DocQADocument, question: str, role: str, ddb: Session | None = None,
                    **context) -> dict:
    """Load the current AI config and answer one question for one role."""
    row = ai_config.get_row(db)
    llm = ai_config.to_llm_config(row)
    return answer(db, doc, question, role, llm if llm.configured else None, DocQASettings.from_config(), ddb,
                  **context)
