import { useEffect, useMemo, useState, type ReactElement } from "react";
import { LuArrowDown, LuChevronDown, LuChevronRight, LuDownload, LuFileCode2, LuFolder, LuFolderOpen, LuRadar, LuSearch, LuSparkles } from "react-icons/lu";
import { AppShell } from "../components/AppShell";
import { GraphCanvas } from "../components/GraphCanvas";
import { useProject } from "../components/ProjectContext";
import { SymbolSearch } from "../components/SymbolSearch";
import { usePageTour } from "../components/Tour";
import { IMPACT_TOUR } from "../components/tours";
import { EmptyState, ErrorBox, TypeDot, useToast } from "../components/ui";
import { api, download, qs } from "../lib/api";
import { TYPE_LABELS, severityTag } from "../lib/format";
import { useSearchParam } from "../lib/router";
import { useApi } from "../lib/hooks";
import type { ImpactExplanation, ImpactItem, ImpactResult, SymbolHit } from "../lib/types";

const SEV_COLOR: Record<string, string> = { High: "#b93a27", Medium: "#c9821b", Low: "#2a8253" };

/** What the current result was run for: a named element or a whole file. */
type Subject = { kind: "target"; id: string } | { kind: "file"; path: string };

export function ImpactPage() {
  const { project, openSource } = useProject();
  const pid = project!.id;
  const toast = useToast();
  const initial = useSearchParam("target");
  const [target, setTarget] = useState(initial ? initial.split(":").slice(1).join(":") || initial : "");
  const [subject, setSubject] = useState<Subject | null>(null);
  const [result, setResult] = useState<ImpactResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("all");
  const [ai, setAi] = useState<{ busy: boolean; data?: ImpactExplanation; error?: string }>({ busy: false });
  const { data: examples } = useApi<SymbolHit[]>(`/projects/${pid}/lineage/examples${qs({ limit: 3 })}`);
  usePageTour("impact", IMPACT_TOUR);

  const show = async (call: () => Promise<ImpactResult>, s: Subject | null) => {
    setBusy(true);
    setError(null);
    setAi({ busy: false });
    try {
      const r = await call();
      setResult(r);
      setFilter("all");
      setSubject(r.ambiguous ? null : s ?? (r.start?.[0] ? { kind: "target", id: r.start[0].id } : null));
    } catch (e) {
      setError((e as Error).message);
      setResult(null);
      setSubject(null);
    } finally {
      setBusy(false);
    }
  };

  const run = (t: string) => {
    if (!t.trim()) return;
    show(() => api.post<ImpactResult>(`/projects/${pid}/impact`, { target: t.trim() }), null);
  };

  const runFile = (path: string) => show(() => api.post<ImpactResult>(`/projects/${pid}/impact/file`, { path }), { kind: "file", path });

  const explain = async () => {
    if (!subject) return;
    setAi({ busy: true });
    try {
      const body = subject.kind === "file" ? { path: subject.path } : { target: subject.id };
      setAi({ busy: false, data: await api.post<ImpactExplanation>(`/projects/${pid}/impact/explain`, body) });
    } catch (e) {
      setAi({ busy: false, error: (e as Error).message });
    }
  };

  useEffect(() => {
    if (initial) run(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const accents = useMemo(() => {
    const m: Record<string, string> = {};
    result?.impacted?.forEach((i) => (m[i.id] = SEV_COLOR[i.severity]));
    result?.start?.forEach((s) => (m[s.id] = "#e9a23b"));
    return m;
  }, [result]);

  const items = (result?.impacted ?? []).filter((i) => filter === "all" || i.severity === filter || i.type === filter);
  const exportQuery = subject?.kind === "file" ? { path: subject.path } : subject ? { target: subject.id } : null;

  return (
    <AppShell title="Impact analysis">
      <div className="page-head">
        <div>
          <h1>Impact analysis</h1>
          <p>Pick a file on the left, or name a column, table, program, copybook or dataset that is about to change. LegacyNexus follows data and control flow to everything that can break.</p>
        </div>
      </div>

      <div className="impact-layout">
        <FileExplorer pid={pid} selected={subject?.kind === "file" ? subject.path : null} onPick={runFile} />

        <div className="stack" style={{ minWidth: 0 }}>
          <div className="card card-pad">
            <form className="row" onSubmit={(e) => { e.preventDefault(); run(target); }}>
              <div data-tour="impact-input" style={{ flex: 1, display: "flex" }}>
                <SymbolSearch projectId={pid} types="column,table,program,copybook,dataset,field,job,api,procedure" placeholder="e.g. CUSTOMER_ADDRESS, ACCOUNT.CBL, ACCOUNT_MASTER"
                  value={target} onChange={setTarget} onPick={(h) => { setTarget(h.name); run(h.id); }} />
              </div>
              <button className="btn btn-primary" type="submit" disabled={busy || !target.trim()} data-tour="impact-run">
                {busy ? <span className="spinner" /> : <LuRadar size={16} />} Analyse impact
              </button>
            </form>
            <div className="row-wrap" style={{ marginTop: 12 }}>
              <span className="subtle">Try:</span>
              {(examples ?? []).map((h) => (
                <button key={h.id} className="chip" onClick={() => { setTarget(h.name.split(".").pop()!); run(h.id); }}>
                  <TypeDot type={h.type} /> {h.name.split(".").pop()}
                </button>
              ))}
            </div>
          </div>

          {error && <ErrorBox message={error} />}

          {result?.ambiguous && (
            <div className="card card-pad">
              <h3 style={{ marginBottom: 10 }}>“{target}” matches more than one element. Which one is changing?</h3>
              <div className="stack-sm">
                {result.options!.map((o) => (
                  <button key={o.id} className="btn" style={{ justifyContent: "flex-start" }} onClick={() => { setTarget(o.label); run(o.id); }}>
                    <TypeDot type={o.type} /> <span className="mono">{o.label}</span> <span className="subtle">{TYPE_LABELS[o.type]}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {result && !result.ambiguous && (
            <>
              <div className="card card-pad">
                <div className="row-wrap" style={{ gap: 6 }}>
                  <span className="subtle">Changing</span>
                  {result.file ? <b className="mono">{result.file}</b> : null}
                  {result.file && <span className="subtle">which contains</span>}
                  {result.start?.map((s) => (
                    <span key={s.id} className="tag"><TypeDot type={s.type} /> <span className="mono">{s.label}</span></span>
                  ))}
                </div>
              </div>

              <div className="stat-row" style={{ gridTemplateColumns: "repeat(4, minmax(0,1fr))" }}>
                <div className="stat"><div className="v">{result.impacted!.length}</div><div className="k">Impacted components</div></div>
                {(["High", "Medium", "Low"] as const).map((s) => (
                  <div key={s} className="stat" style={{ cursor: "pointer" }} onClick={() => setFilter(filter === s ? "all" : s)}>
                    <div className="v" style={{ color: SEV_COLOR[s] }}>{result.severity_counts?.[s] ?? 0}</div>
                    <div className="k">{s} severity {filter === s && "· filtered"}</div>
                  </div>
                ))}
              </div>

              <AiCard state={ai} onExplain={explain} disabled={!subject} />

              {result.impacted!.length === 0 ? (
                <div className="card"><EmptyState title="Nothing downstream">No program, table, dataset or API depends on this.</EmptyState></div>
              ) : (
                <>
                  {result.representative_chain && result.representative_chain.length > 1 && (
                    <div className="card card-pad">
                      <h3 style={{ marginBottom: 12 }}>Longest impact path</h3>
                      <div className="row-wrap" style={{ gap: 6 }}>
                        {result.representative_chain.map((n, i) => (
                          <span key={n.id} className="row" style={{ gap: 6 }}>
                            {i > 0 && <LuArrowDown size={14} style={{ transform: "rotate(-90deg)", color: "var(--text-3)" }} />}
                            <span className="tag" style={{ height: 28, fontSize: 13 }}><TypeDot type={n.type} /> <span className="mono">{n.label}</span></span>
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  <div className="card" style={{ overflow: "hidden" }}>
                    <div className="card-head"><h3>Impact graph</h3><span className="subtle">Amber: the change · red/amber/green rings: severity</span></div>
                    <div className="graph-stage" style={{ height: 420 }}>
                      <GraphCanvas graph={result.graph!} nodeAccent={accents} />
                    </div>
                  </div>
                  <div className="card">
                    <div className="card-head">
                      <h3>Impacted components</h3>
                      <div className="row">
                        <select className="select" style={{ width: 170, height: 32 }} value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter">
                          <option value="all">All types</option>
                          {Object.keys(result.summary ?? {}).map((t) => <option key={t} value={t}>{TYPE_LABELS[t] ?? t} ({result.summary![t]})</option>)}
                        </select>
                        {exportQuery && (
                          <button className="btn btn-sm" onClick={() => download(`/projects/${pid}/impact/export${qs(exportQuery)}`, "impact.csv").catch((e) => toast(e.message, true))}>
                            <LuDownload size={14} /> Export CSV
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="table-wrap">
                      <table className="data">
                        <thead><tr><th>Component</th><th>Severity</th><th>Why it is affected</th><th>Evidence</th></tr></thead>
                        <tbody>{items.map((i) => <ImpactRow key={i.id} item={i} openSource={openSource} />)}</tbody>
                      </table>
                    </div>
                  </div>
                </>
              )}
            </>
          )}

          {!result && !error && !busy && (
            <div className="card"><EmptyState title="Pick a file or name an element">Select a file in the explorer to see everything that depends on it.</EmptyState></div>
          )}
        </div>
      </div>
    </AppShell>
  );
}

function AiCard({ state, onExplain, disabled }: { state: { busy: boolean; data?: ImpactExplanation; error?: string }; onExplain: () => void; disabled: boolean }) {
  const d = state.data;
  return (
    <div className="card card-pad ai-card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h3>Explain with AI</h3>
          {!d && <p className="subtle" style={{ marginTop: 4 }}>A plain-language summary of this impact for business and delivery leads. Optional; uses the AI model set up in Settings.</p>}
        </div>
        {!d && (
          <button className="btn" onClick={onExplain} disabled={disabled || state.busy}>
            {state.busy ? <span className="spinner" /> : <LuSparkles size={15} />} Explain
          </button>
        )}
      </div>
      {state.error && <div style={{ marginTop: 10 }}><ErrorBox message={state.error} /></div>}
      {d && (
        <div style={{ marginTop: 8 }}>
          <p>{d.summary}</p>
          {d.business_effect.length > 0 && <><b>Business effect</b><ul>{d.business_effect.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
          {d.risks.length > 0 && <div style={{ marginTop: 8 }}><b>Risks</b><ul>{d.risks.map((x, i) => <li key={i}>{x}</li>)}</ul></div>}
          {d.test_first.length > 0 && <div style={{ marginTop: 8 }}><b>Test these first</b><ul>{d.test_first.map((x, i) => <li key={i}>{x}</li>)}</ul></div>}
          {d.model && <p className="subtle" style={{ marginTop: 8, fontSize: 12 }}>Written by {d.model}{d.cached ? " (from cache, no cost)" : ""}. The impact list above is calculated from the code and is the source of truth.</p>}
        </div>
      )}
    </div>
  );
}

type FileRow = { path: string; language: string | null };
type TreeNode = { name: string; path: string; children: Map<string, TreeNode>; file?: FileRow };

function buildTree(files: FileRow[]): TreeNode {
  const root: TreeNode = { name: "", path: "", children: new Map() };
  for (const f of files) {
    const parts = f.path.split("/");
    let node = root;
    parts.forEach((part, i) => {
      const path = parts.slice(0, i + 1).join("/");
      if (!node.children.has(part)) node.children.set(part, { name: part, path, children: new Map() });
      node = node.children.get(part)!;
    });
    node.file = f;
  }
  return root;
}

function FileExplorer({ pid, selected, onPick }: { pid: number; selected: string | null; onPick: (path: string) => void }) {
  const { data: files } = useApi<FileRow[]>(`/projects/${pid}/files`);
  const { data: analysable } = useApi<{ files: Record<string, string[]> }>(`/projects/${pid}/impact/files`);
  const [q, setQ] = useState("");
  const [onlyAnalysable, setOnlyAnalysable] = useState(true);
  const [open, setOpen] = useState<Set<string>>(new Set());

  const comps = (path: string) => analysable?.files[path.toLowerCase().replace(/^[./]+/, "")];
  const shown = useMemo(() => (files ?? []).filter((f) => (!onlyAnalysable || comps(f.path)) && f.path.toLowerCase().includes(q.toLowerCase())),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [files, analysable, onlyAnalysable, q]);
  const tree = useMemo(() => buildTree(shown), [shown]);
  const searching = q.trim().length > 0;

  const toggle = (path: string) => setOpen((s) => { const n = new Set(s); if (n.has(path)) n.delete(path); else n.add(path); return n; });

  const render = (node: TreeNode, depth: number): ReactElement[] =>
    [...node.children.values()]
      .sort((a, b) => Number(!!a.file) - Number(!!b.file) || a.name.localeCompare(b.name))
      .flatMap((c) => {
        const pad = { paddingLeft: 8 + depth * 14 };
        if (c.file) {
          const has = comps(c.file.path);
          return [(
            <button key={c.path} className={`tree-row ${selected === c.file.path ? "active" : ""} ${has ? "" : "dim"}`} style={pad}
              title={has ? `Contains ${has.join(", ")}` : "Nothing to analyse in this file"} onClick={() => has && onPick(c.file!.path)}>
              <LuFileCode2 size={14} /><span className="name mono">{c.name}</span>
            </button>
          )];
        }
        const autoOpen = depth === 0 && node.children.size === 1; // a single top folder starts open
        const isOpen = searching || open.has(c.path) !== autoOpen;
        return [(
          <button key={c.path} className="tree-row" style={pad} onClick={() => toggle(c.path)}>
            {isOpen ? <LuChevronDown size={14} /> : <LuChevronRight size={14} />}
            {isOpen ? <LuFolderOpen size={14} /> : <LuFolder size={14} />}
            <span className="name">{c.name}</span>
          </button>
        ), ...(isOpen ? render(c, depth + 1) : [])];
      });

  return (
    <div className="card explorer">
      <div className="card-head"><h3>Files</h3><span className="subtle">{shown.length}</span></div>
      <div style={{ padding: 10, borderBottom: "1px solid var(--line)" }}>
        <div style={{ position: "relative" }}>
          <LuSearch size={15} style={{ position: "absolute", left: 12, top: 10, color: "var(--text-3)" }} />
          <input className="input" style={{ paddingLeft: 34, height: 34 }} placeholder="Filter files" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <label className="check" style={{ marginTop: 8, fontSize: 13 }}>
          <input type="checkbox" checked={onlyAnalysable} onChange={(e) => setOnlyAnalysable(e.target.checked)} /> Only files with code to analyse
        </label>
      </div>
      <div className="explorer-list">
        {!files ? <div className="subtle" style={{ padding: 8 }}>Loading…</div>
          : shown.length === 0 ? <div className="subtle" style={{ padding: 8 }}>No files match.</div>
          : render(tree, 0)}
      </div>
    </div>
  );
}

function ImpactRow({ item, openSource }: { item: ImpactItem; openSource: (f: string, s?: number, e?: number) => void }) {
  return (
    <tr>
      <td style={{ minWidth: 200 }}>
        <div className="row" style={{ gap: 8 }}><TypeDot type={item.type} /><b className="mono" style={{ fontSize: 13 }}>{item.label}</b></div>
        <div className="subtle">{TYPE_LABELS[item.type] ?? item.type} · {item.distance} hop{item.distance === 1 ? "" : "s"}</div>
      </td>
      <td>
        <span className={`tag ${severityTag(item.severity)}`}>{item.severity}</span>
        <div className="subtle" style={{ marginTop: 4 }}>{item.impact_type === "control" ? "decision depends on it" : item.impact_type === "data" ? "data flows into it" : "structural dependency"}</div>
      </td>
      <td style={{ fontSize: 13 }}>
        {item.path.map((p, i) => (
          <span key={p.id}>{i > 0 && <span className="subtle"> → </span>}<span className="mono">{p.label}</span></span>
        ))}
      </td>
      <td>
        <div className="row-wrap" style={{ gap: 4 }}>
          {item.evidence.filter((h) => h.file).map((h, i) => (
            <button key={i} className="cite" title={h.how} onClick={() => openSource(h.file!, h.line)}>{h.file!.split("/").pop()}:{h.line}</button>
          ))}
        </div>
      </td>
    </tr>
  );
}
