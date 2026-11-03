import { useEffect, useRef, useState } from "react";
import { LuBadgeCheck, LuBraces, LuCircleAlert, LuCircleCheck, LuSend, LuShieldCheck, LuTriangleAlert } from "react-icons/lu";
import { AppShell } from "../components/AppShell";
import { useProject } from "../components/ProjectContext";
import { usePageTour } from "../components/Tour";
import { ASK_TOUR } from "../components/tours";
import { SemanticIndexCard } from "../components/SemanticIndexCard";
import { ConversationBar, FollowUpNote } from "../components/Conversation";
import { ConfidenceRing, ErrorBox, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import { Link } from "../lib/router";
import type { Answer, Conversation } from "../lib/types";

export function AskPage() {
  const { project } = useProject();
  const pid = project!.id;
  const toast = useToast();
  const { data: suggestions } = useApi<string[]>(`/projects/${pid}/qa/suggestions`);
  const { data: ai } = useApi<{ configured: boolean; provider: string; model: string; deployment: string }>("/settings/ai");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [convId, setConvId] = useState<number | null>(null); // null = the next question starts a new conversation
  const [thread, setThread] = useState<Answer[]>([]);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  usePageTour("ask", ASK_TOUR);

  const loadConversations = (selectLatest = false) =>
    api.get<Conversation[]>(`/projects/${pid}/conversations`).then((list) => {
      setConversations(list);
      if (selectLatest) setConvId(list[0]?.id ?? null);
    }).catch(() => setConversations([]));

  useEffect(() => { loadConversations(true); }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps

  // Show the selected conversation.
  useEffect(() => {
    if (convId === null) { setThread([]); return; }
    let live = true;
    api.get<{ entries: Answer[] }>(`/projects/${pid}/conversations/${convId}`)
      .then((c) => { if (live) setThread(c.entries); })
      .catch(() => { if (live) setThread([]); });
    return () => { live = false; };
  }, [pid, convId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [thread.length, pending]);

  const ask = async (q: string) => {
    const text = q.trim();
    if (text.length < 3 || pending) return;
    setPending(text);
    setQuestion("");
    setError(null);
    try {
      const res = await api.post<Answer>(`/projects/${pid}/ask`, convId === null
        ? { question: text, new_conversation: true }
        : { question: text, conversation_id: convId });
      setThread((t) => [...t, res]);
      if (res.conversation_id && res.conversation_id !== convId) setConvId(res.conversation_id);
      loadConversations();
    } catch (e) {
      setError((e as Error).message);
      setQuestion(text);
    } finally {
      setPending(null);
    }
  };

  const removeConversation = async (id: number) => {
    if (!confirm("Delete this conversation and its questions?")) return;
    try {
      await api.del(`/projects/${pid}/conversations/${id}`);
      toast("Conversation deleted");
      setConvId(null);
      loadConversations(true);
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  return (
    <AppShell title="Ask the code">
      <div className="ask-layout">
        <div className="thread" style={{ minHeight: "calc(100vh - 180px)" }}>
          <ConversationBar conversations={conversations} currentId={convId} disabled={!!pending}
            onSelect={setConvId} onNew={() => { setConvId(null); setError(null); }} onDelete={removeConversation} />
          {thread.length === 0 && !pending && (
            <div className="card card-pad" style={{ textAlign: "center", padding: "48px 24px" }}>
              <h2>Ask anything about {project!.name}</h2>
              <p className="muted" style={{ maxWidth: 560, margin: "8px auto 0" }}>
                Where something is calculated, which programs use a table, what a job does, what a validation checks.
                Answers cite file and line, and say “Not found in the provided code” when the evidence is not there.
                Follow-up questions such as “who calls it?” or “what about CUSTOMER_MASTER?” are understood.
              </p>
            </div>
          )}
          {thread.map((a, i) => (
            <div key={a.id ?? i} className="stack-sm" style={{ gap: 10 }}>
              <div className="q-bubble">{a.question}</div>
              <FollowUpNote context={a.context} />
              <AnswerCard a={a} />
            </div>
          ))}
          {pending && (
            <div className="stack-sm" style={{ gap: 10 }}>
              <div className="q-bubble">{pending}</div>
              <div className="card answer row"><span className="spinner" /> Retrieving evidence and checking citations…</div>
            </div>
          )}
          {error && <ErrorBox message={error} />}
          <div ref={bottom} />
          <div className="ask-box">
            <form className="ask-input" data-tour="ask-input" onSubmit={(e) => { e.preventDefault(); ask(question); }}>
              <textarea
                value={question}
                placeholder="e.g. Where is interest calculated?"
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    ask(question);
                  }
                }}
                rows={1}
                aria-label="Your question"
              />
              <button className="btn btn-primary" type="submit" disabled={!!pending || question.trim().length < 3}>
                <LuSend size={15} /> Ask
              </button>
            </form>
            <div className="subtle" style={{ padding: "6px 4px" }}>Enter to send · Shift+Enter for a new line</div>
          </div>
        </div>

        <aside className="stack">
          <div className="card card-pad" data-tour="ask-suggest">
            <h3 style={{ marginBottom: 12 }}>Try asking</h3>
            <div className="suggest">
              {(suggestions ?? []).map((s) => <button key={s} onClick={() => ask(s)}>{s}</button>)}
            </div>
          </div>
          <SemanticIndexCard projectId={pid} />
          <div className="card card-pad" data-tour="ask-trust">
            <h3 className="row" style={{ marginBottom: 10 }}><LuShieldCheck size={18} color="var(--teal)" /> How answers are checked</h3>
            <ul className="muted" style={{ fontSize: 13.5, paddingLeft: 18, margin: 0, display: "flex", flexDirection: "column", gap: 6 }}>
              <li>Code is found by keyword search, plus meaning-based search when semantic search is on; the two rankings are merged.</li>
              <li>Only the top-ranked excerpts are sent, never whole files.</li>
              <li>The model must return JSON: answer, claims, citations, confidence, not-found flag.</li>
              <li>Every cited file, line range and identifier is checked against the repository.</li>
              <li>Weak or unverifiable answers become “Not found in the provided code”.</li>
            </ul>
          </div>
          <div className="card card-pad">
            <h3 style={{ marginBottom: 6 }}>AI model</h3>
            {ai?.configured ? (
              <p className="muted" style={{ fontSize: 14 }}><span className="tag tag-green">Connected</span> {ai.provider} · <span className="mono">{ai.deployment || ai.model}</span></p>
            ) : (
              <p className="muted" style={{ fontSize: 14 }}>
                No model configured. You will get the static-analysis evidence for each question.{" "}
                <Link to="/settings">Configure a model</Link> for written answers.
              </p>
            )}
          </div>
        </aside>
      </div>
    </AppShell>
  );
}

function AnswerCard({ a }: { a: Answer }) {
  const { openSource } = useProject();
  const [showJson, setShowJson] = useState(false);
  const [showEvidence, setShowEvidence] = useState(false);
  const v = a.validation || {};
  const modeTag = a.mode === "graph"
    ? <span className="tag tag-green">Exact answer from the knowledge graph · no AI cost</span>
    : a.mode === "llm"
      ? <span className="tag tag-blue">AI answer · {a.model}{a.usage?.cached ? " · from cache, no tokens used" : ""}</span>
      : a.mode === "evidence_only" ? <span className="tag">Static-analysis evidence</span> : <span className="tag tag-amber">Abstained</span>;

  const structured = {
    answer: a.answer, not_found: a.not_found, confidence: a.confidence,
    evidence: a.claims.map((c) => ({ claim: c.text, citations: c.citations.map((x) => ({ file: x.file, line_start: x.line_start, line_end: x.line_end, verified: x.valid })) })),
  };

  return (
    <div className={`card answer ${a.not_found ? "notfound" : ""}`}>
      <div className="row" style={{ alignItems: "flex-start", marginBottom: 14 }}>
        <ConfidenceRing value={a.confidence} label={a.confidence_label} />
        <div className="spacer" />
        <div className="row-wrap" style={{ justifyContent: "flex-end" }}>
          {modeTag}
          {v.citations_total !== undefined && v.citations_total > 0 && (
            <span className={`tag ${v.citations_valid === v.citations_total ? "tag-green" : "tag-amber"}`}>
              <LuBadgeCheck size={13} /> {v.citations_valid}/{v.citations_total} citations verified
            </span>
          )}
        </div>
      </div>
      <div className="text">{a.not_found && <LuCircleAlert size={16} style={{ verticalAlign: -2, marginRight: 6 }} />}{a.answer}</div>
      {a.abstain_reason && <div className="subtle" style={{ marginTop: 6 }}>{a.abstain_reason}</div>}
      {a.warning && <div className="warn-box" style={{ marginTop: 10 }}>{a.warning}</div>}
      {v.unknown_identifiers && v.unknown_identifiers.length > 0 && (
        <div className="warn-box" style={{ marginTop: 10 }}>
          <LuTriangleAlert size={14} style={{ verticalAlign: -2 }} /> Names not found in the code: {v.unknown_identifiers.join(", ")}
        </div>
      )}

      {a.claims.length > 0 && (
        <div style={{ marginTop: 16 }}>
          {a.claims.map((c, i) => (
            <div className="claim" key={i}>
              <span className="mark">{c.supported ? <LuCircleCheck size={16} color="var(--green)" /> : <LuCircleAlert size={16} color="var(--red)" />}</span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 14 }}>{c.text}</div>
                <div className="row-wrap" style={{ marginTop: 6, gap: 6 }}>
                  {c.citations.map((x, j) => (
                    <button key={j} className={`cite ${x.valid ? "" : "bad"}`} title={x.reason ?? "Verified against the repository"}
                      onClick={() => x.valid && openSource(x.file, x.line_start, x.line_end)}>
                      {x.file.split("/").pop()}:{x.line_start}{x.line_end !== x.line_start ? `-${x.line_end}` : ""}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ))}
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

      <div className="row" style={{ marginTop: 16, borderTop: "1px solid var(--line)", paddingTop: 12 }}>
        <button className="btn btn-ghost btn-sm" onClick={() => setShowEvidence((s) => !s)}>
          {showEvidence ? "Hide" : "Show"} retrieved evidence ({a.evidence.length})
        </button>
        <button className="btn btn-ghost btn-sm" onClick={() => setShowJson((s) => !s)}><LuBraces size={14} /> {showJson ? "Hide" : "View"} JSON</button>
        <div className="spacer" />
        {a.retrieval && a.retrieval.mode !== "graph" && (
          <span className="subtle">
            {a.retrieval.mode === "hybrid" ? "Hybrid search" : "Keyword search"} · {a.retrieval.chunks_sent} excerpts · {(a.retrieval.chars_sent / 1000).toFixed(1)}k chars sent
          </span>
        )}
        {a.retrieval?.mode === "graph" && <span className="subtle">Answered by a graph query · nothing sent to an AI model</span>}
        {a.usage && a.mode === "llm" && <span className="subtle">· {a.usage.cached ? "0 tokens (cached)" : `${a.usage.input_tokens + a.usage.output_tokens} tokens`} · {(a.usage.latency_ms / 1000).toFixed(1)}s</span>}
        {!!a.usage?.redactions && <span className="subtle">· {a.usage.redactions} secret(s) redacted before sending</span>}
      </div>
      {showEvidence && (
        <div className="stack-sm" style={{ marginTop: 10 }}>
          {a.facts.length > 0 && (
            <div className="card-pad" style={{ background: "var(--surface-2)", borderRadius: 8, padding: 12 }}>
              <div className="label" style={{ marginBottom: 6 }}>Verified facts from the knowledge graph</div>
              {a.facts.map((f, i) => (
                <div key={i} className="row" style={{ fontSize: 13, padding: "2px 0" }}>
                  <span style={{ flex: 1 }}>{f.text}</span>
                  <button className="cite" onClick={() => openSource(f.file, f.line)}>{f.file.split("/").pop()}:{f.line}</button>
                </div>
              ))}
            </div>
          )}
          {a.evidence.map((e, i) => (
            <div key={i} style={{ border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
              <div className="row" style={{ padding: "8px 12px", background: "var(--surface-2)", fontSize: 13 }}>
                <b>{e.title}</b><span className="spacer" />
                <span className={`tag ${e.found_by === "both" ? "tag-green" : e.found_by === "semantic" ? "tag-violet" : "tag-blue"}`}>
                  {e.found_by === "both" ? "keyword + semantic" : e.found_by}
                </span>
                <span className="subtle">
                  {e.score !== null && e.score !== undefined ? `score ${e.score}` : ""}
                  {e.similarity !== null && e.similarity !== undefined ? ` · similarity ${e.similarity.toFixed(2)}` : ""}
                </span>
                <button className="cite" onClick={() => openSource(e.file, e.start_line, e.end_line)}>{e.file.split("/").pop()}:{e.start_line}-{e.end_line}</button>
              </div>
              <pre className="mono" style={{ margin: 0, padding: 12, fontSize: 12, background: "#0f1d33", color: "#d5dfec", overflowX: "auto" }}>{e.snippet}</pre>
            </div>
          ))}
        </div>
      )}
      {showJson && <pre className="mono" style={{ marginTop: 10, padding: 12, fontSize: 12, background: "#0f1d33", color: "#d5dfec", borderRadius: 8, overflowX: "auto" }}>{JSON.stringify(structured, null, 2)}</pre>}
    </div>
  );
}
