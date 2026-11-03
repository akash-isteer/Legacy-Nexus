import { useEffect, useMemo, useRef, useState } from "react";
import { LuBadgeCheck, LuBraces, LuCircleAlert, LuFileText, LuPaperclip, LuSend, LuTriangleAlert, LuX } from "react-icons/lu";
import { AppShell } from "../components/AppShell";
import { ConversationBar, FollowUpNote } from "../components/Conversation";
import { Markdown } from "../components/Markdown";
import { ConfidenceRing, useToast } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { SIGNIN_ROLES, useAuth } from "../lib/auth";
import { useApi } from "../lib/hooks";
import type { Conversation, DocAnswer, DocCitation, DocDocument } from "../lib/types";

const ACCEPT = ".pdf,.docx,.txt,.md,.markdown,.rst";
const accepted = (name: string) => /\.(pdf|docx|txt|md|markdown|rst)$/i.test(name);

export function DocQAPage() {
  const toast = useToast();
  const { signinRole } = useAuth();
  const role = signinRole; // answers are written for the role chosen at sign-in
  const roleLabel = SIGNIN_ROLES.find((r) => r.id === role)?.label ?? role;
  const { data: documents, reload: reloadDocs } = useApi<DocDocument[]>("/doc-qa/documents");

  const [docId, setDocId] = useState<number | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [convId, setConvId] = useState<number | null>(null); // null = the next question starts a new conversation
  const [question, setQuestion] = useState("");
  const [thread, setThread] = useState<DocAnswer[]>([]);
  const [pending, setPending] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const currentDoc = useMemo(() => documents?.find((d) => d.id === docId) ?? null, [documents, docId]);

  // Pick up the most recent document once the list loads.
  useEffect(() => {
    if (docId === null && documents && documents.length > 0) setDocId(documents[0].id);
  }, [documents, docId]);

  const loadConversations = (id: number, selectLatest = false) =>
    api.get<Conversation[]>(`/doc-qa/documents/${id}/conversations`).then((list) => {
      setConversations(list);
      if (selectLatest) setConvId(list[0]?.id ?? null);
    }).catch(() => setConversations([]));

  // Each document has its own conversations; open the latest one when the document changes.
  useEffect(() => {
    setConversations([]);
    setConvId(null);
    if (docId !== null) loadConversations(docId, true);
  }, [docId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Show the selected conversation.
  useEffect(() => {
    if (docId === null || convId === null) { setThread([]); return; }
    let live = true;
    api.get<{ entries: DocAnswer[] }>(`/doc-qa/documents/${docId}/conversations/${convId}`)
      .then((c) => { if (live) setThread(c.entries); })
      .catch(() => { if (live) setThread([]); });
    return () => { live = false; };
  }, [docId, convId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [thread.length, pending]);

  const upload = async (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    if (!accepted(file.name)) {
      setError("Unsupported file type. Attach a PDF, Word (.docx), text (.txt) or Markdown (.md) file.");
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file, file.name);
      const doc = await api.upload<DocDocument>("/doc-qa/documents", form);
      setDocId(doc.id);
      setThread([]);
      reloadDocs();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const ask = async () => {
    const text = question.trim();
    if (text.length < 3 || pending || docId === null) return;
    setPending(text);
    setQuestion("");
    setError(null);
    try {
      const res = await api.post<DocAnswer>(`/doc-qa/documents/${docId}/ask`, convId === null
        ? { question: text, role, new_conversation: true }
        : { question: text, role, conversation_id: convId });
      setThread((t) => [...t, res]);
      if (res.conversation_id && res.conversation_id !== convId) setConvId(res.conversation_id);
      loadConversations(docId);
    } catch (e) {
      setError((e as Error).message);
      setQuestion(text);
    } finally {
      setPending(null);
    }
  };

  const removeDoc = async (id: number) => {
    if (!confirm("Remove this document and its question history?")) return;
    try {
      await api.del(`/doc-qa/documents/${id}`);
      toast("Document removed");
      if (docId === id) setDocId(null);
      reloadDocs();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const removeConversation = async (id: number) => {
    if (docId === null || !confirm("Delete this conversation and its questions?")) return;
    try {
      await api.del(`/doc-qa/documents/${docId}/conversations/${id}`);
      toast("Conversation deleted");
      setConvId(null);
      loadConversations(docId, true);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const canSend = !pending && !uploading && question.trim().length >= 3 && docId !== null;
  const others = (documents ?? []).filter((d) => d.id !== docId);

  return (
    <AppShell title="Document Q&A">
      <div className="docqa chat">
        <header className="row" style={{ gap: 10, flexWrap: "wrap" }}>
          <h1 style={{ margin: 0 }}>Document Q&amp;A</h1>
          <span className="tag tag-violet" title="Chosen when you signed in. Sign out to change it.">Answering as {roleLabel}</span>
          <div className="spacer" />
          {currentDoc && (
            <ConversationBar conversations={conversations} currentId={convId} disabled={!!pending}
              onSelect={setConvId} onNew={() => { setConvId(null); setError(null); }} onDelete={removeConversation} />
          )}
        </header>

        <div className="chat-thread">
          {thread.length === 0 && !pending && (
            <div className="chat-empty">
              <LuFileText size={28} color="var(--text-3)" />
              <h2>{currentDoc ? `Ask anything about ${currentDoc.filename}` : "Attach a document to start"}</h2>
              <p className="muted">
                {currentDoc
                  ? "Answers come only from this document and cite where they came from."
                  : <>Use the <LuPaperclip size={14} style={{ verticalAlign: -2 }} /> button below. PDF, Word, text or Markdown.</>}
              </p>
            </div>
          )}

          {thread.map((a, i) => (
            <div key={a.id ?? `t${i}`} className="stack-sm" style={{ gap: 10 }}>
              <div className="q-bubble">{a.question}</div>
              <FollowUpNote context={a.context} />
              <DocAnswerCard a={a} />
            </div>
          ))}

          {pending && (
            <div className="stack-sm" style={{ gap: 10 }}>
              <div className="q-bubble">{pending}</div>
              <div className="card answer row"><span className="spinner" /> Reading the document and writing the answer…</div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Message box pinned to the bottom, with the attach button on the left. */}
        <div className="ask-box">
          {error && <div className="error-box" style={{ marginBottom: 8 }}>{error}</div>}
          <form
            className={`ask-input composer ${dragOver ? "over" : ""}`}
            onSubmit={(e) => { e.preventDefault(); ask(); }}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => { e.preventDefault(); setDragOver(false); upload(e.dataTransfer.files); }}
          >
            {currentDoc && (
              <div className="attach-row">
                <span className="attach-chip" title={currentDoc.filename}>
                  <LuFileText size={14} />
                  <span className="name">{currentDoc.filename}</span>
                  <span className="subtle">{currentDoc.format.toUpperCase()}{currentDoc.page_count ? ` · ${currentDoc.page_count} pages` : ""}</span>
                  <button type="button" className="x" onClick={() => removeDoc(currentDoc.id)} title="Remove this document" aria-label="Remove this document"><LuX size={13} /></button>
                </span>
                {others.length > 0 && (
                  <select className="select attach-switch" value="" onChange={(e) => e.target.value && setDocId(Number(e.target.value))} aria-label="Switch document">
                    <option value="">Switch document…</option>
                    {others.map((d) => <option key={d.id} value={d.id}>{d.filename}</option>)}
                  </select>
                )}
              </div>
            )}
            <div className="composer-row">
              <button type="button" className="btn btn-ghost btn-icon attach-btn" onClick={() => fileInput.current?.click()}
                disabled={uploading} title="Attach a document (PDF, Word, text, Markdown)" aria-label="Attach a document">
                {uploading ? <span className="spinner" /> : <LuPaperclip size={18} />}
              </button>
              <input ref={fileInput} type="file" accept={ACCEPT} hidden onChange={(e) => upload(e.target.files)} />
              <textarea
                value={question}
                placeholder={uploading ? "Reading the document…" : currentDoc ? "Ask a question about the document" : "Attach a document first"}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } }}
                rows={1}
                disabled={!currentDoc}
                aria-label="Your question"
              />
              <button className="btn btn-primary btn-icon" type="submit" disabled={!canSend} title="Send" aria-label="Send"><LuSend size={16} /></button>
            </div>
          </form>
          <div className="subtle" style={{ marginTop: 6, textAlign: "center", fontSize: 12 }}>Enter to send · Shift+Enter for a new line</div>
        </div>
      </div>
    </AppShell>
  );
}

/* ---------------- answer rendering ---------------- */

function modeTag(a: DocAnswer) {
  if (a.mode === "llm") return <span className="tag tag-blue">AI answer · {a.model}</span>;
  if (a.mode === "evidence_only") return <span className="tag">Relevant passages</span>;
  return <span className="tag tag-amber">Insufficient information</span>;
}

function DocAnswerCard({ a }: { a: DocAnswer }) {
  const [showEvidence, setShowEvidence] = useState(false);
  const [showJson, setShowJson] = useState(false);
  const v = a.validation || {};

  return (
    <div className={`card answer ${a.not_found ? "notfound" : ""}`}>
      <div className="row-wrap" style={{ alignItems: "flex-start", marginBottom: 14, gap: 8 }}>
        <ConfidenceRing value={a.confidence} label={a.confidence_label} />
        <div className="spacer" />
        <div className="row-wrap" style={{ justifyContent: "flex-end" }}>
          <span className="tag tag-violet">{a.role_label}</span>
          {modeTag(a)}
          {v.citations_total !== undefined && v.citations_total > 0 && (
            <span className={`tag ${v.citations_valid === v.citations_total ? "tag-green" : "tag-amber"}`}>
              <LuBadgeCheck size={13} /> {v.citations_valid}/{v.citations_total} citations verified
            </span>
          )}
        </div>
      </div>

      {a.not_found
        ? <div className="text"><LuCircleAlert size={16} style={{ verticalAlign: -2, marginRight: 6 }} />{a.answer}</div>
        : <Markdown text={a.answer} />}

      {a.abstain_reason && <div className="subtle" style={{ marginTop: 6 }}>{a.abstain_reason}</div>}
      {a.missing && <div className="warn-box" style={{ marginTop: 10 }}><LuTriangleAlert size={14} style={{ verticalAlign: -2 }} /> Not covered by the document: {a.missing}</div>}
      {a.warning && <div className="warn-box" style={{ marginTop: 10 }}>{a.warning}</div>}

      {a.citations.length > 0 && (
        <div className="stack-sm" style={{ marginTop: 14 }}>
          <div className="label">Sources</div>
          {a.citations.map((c, i) => <CitationRow key={i} c={c} />)}
        </div>
      )}

      {a.confidence_breakdown.length > 0 && (
        <details style={{ marginTop: 14 }}>
          <summary className="subtle" style={{ cursor: "pointer" }}>How the confidence score was computed</summary>
          <div className="breakdown" style={{ marginTop: 10 }}>
            {a.confidence_breakdown.map((p) => (
              <div key={p.name} style={{ display: "contents" }}>
                <span>{p.name} <span className="subtle">×{p.weight}</span></span>
                <div className="bar"><span style={{ width: `${p.value * 100}%` }} /></div>
                <span className="num">{p.value.toFixed(2)}</span>
              </div>
            ))}
          </div>
        </details>
      )}

      <div className="row-wrap" style={{ marginTop: 12, gap: 8, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
        <button className="btn btn-ghost btn-sm" onClick={() => setShowEvidence((s) => !s)}>
          {showEvidence ? "Hide" : "Show"} retrieved passages ({a.evidence.length})
        </button>
        <button className="btn btn-ghost btn-sm" onClick={() => setShowJson((s) => !s)}><LuBraces size={14} /> {showJson ? "Hide" : "View"} JSON</button>
        <div className="spacer" />
        {a.usage && <span className="subtle">{a.usage.input_tokens + a.usage.output_tokens} tokens · {(a.usage.latency_ms / 1000).toFixed(1)}s</span>}
      </div>

      {showEvidence && (
        <div className="stack-sm" style={{ marginTop: 10 }}>
          {a.evidence.map((e, i) => (
            <div key={i} style={{ border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
              <div className="row" style={{ padding: "8px 12px", background: "var(--surface-2)", fontSize: 13 }}>
                <b>[S{e.source}]</b> <span>{e.title}</span>
              </div>
              <div style={{ margin: 0, padding: 12, fontSize: 13, whiteSpace: "pre-wrap", color: "var(--text-2)" }}>{e.snippet}</div>
            </div>
          ))}
        </div>
      )}
      {showJson && (
        <pre className="mono" style={{ marginTop: 10, padding: 12, fontSize: 12, background: "#0f1d33", color: "#d5dfec", borderRadius: 8, overflowX: "auto" }}>
          {JSON.stringify({ role: a.role, answer: a.answer, not_found: a.not_found, confidence: a.confidence,
            citations: a.citations.map((c) => ({ source: c.source, page: c.page, section: c.section, verified: c.valid })),
            missing: a.missing }, null, 2)}
        </pre>
      )}
    </div>
  );
}

function CitationRow({ c }: { c: DocCitation }) {
  const [open, setOpen] = useState(false);
  const where = c.title || (c.page ? `page ${c.page}` : "excerpt");
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: "8px 10px" }}>
      <div className="row-wrap" style={{ gap: 8 }}>
        <span className={`cite ${c.valid ? "" : "bad"}`} title={c.reason ?? "Verified against the document"}
          onClick={() => c.snippet && setOpen((s) => !s)} style={{ cursor: c.snippet ? "pointer" : "default" }}>
          [S{c.source}] {where}
        </span>
        {c.note && <span className="subtle" style={{ flex: 1, minWidth: 0 }}>{c.note}</span>}
        {!c.valid && <span className="tag tag-amber">unverified</span>}
      </div>
      {open && c.snippet && <div className="subtle" style={{ marginTop: 8, whiteSpace: "pre-wrap", fontSize: 13 }}>{c.snippet}</div>}
    </div>
  );
}
