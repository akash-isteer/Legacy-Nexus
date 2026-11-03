import { useMemo, useRef, useState, type ReactNode } from "react";
import { LuCode, LuImage, LuLayers, LuMaximize, LuX, LuZoomIn, LuZoomOut } from "react-icons/lu";
import { AppShell } from "../components/AppShell";
import { BusinessMap } from "../components/BusinessMap";
import { GraphCanvas, type GraphHandle } from "../components/GraphCanvas";
import { BlastRadiusCard, GraphHud } from "../components/GraphInsights";
import { useProject } from "../components/ProjectContext";
import { SymbolSearch } from "../components/SymbolSearch";
import { usePageTour } from "../components/Tour";
import { GRAPH_TOUR } from "../components/tours";
import { ErrorBox, Loading, TypeDot } from "../components/ui";
import { qs } from "../lib/api";
import { TYPE_LABELS, criticalityTag } from "../lib/format";
import { useApi } from "../lib/hooks";
import { navigate } from "../lib/router";
import type { BlastRadius, Graph, GraphEdge, GraphNode, ProgramStats, Rule } from "../lib/types";

const ALL_TYPES = ["job", "program", "table", "dataset", "api", "procedure", "external", "copybook", "unresolved", "config"];

type NodeDetail = {
  node: GraphNode;
  incoming: GraphEdge[];
  outgoing: GraphEdge[];
  stats?: ProgramStats;
  paragraphs?: { name: string; start_line: number; end_line: number }[];
  parameters?: string[];
  rules?: Rule[];
  definition?: { name: string; type: string; primary_key: boolean; nullable: boolean; line: number }[];
  blast_radius?: BlastRadius;
};

const VIEW_KEY = "lnx.graph.view";

export function GraphPage() {
  const { project } = useProject();
  const pid = project!.id;
  const [view, setView] = useState<"business" | "technical">(() => {
    try {
      return localStorage.getItem(VIEW_KEY) === "technical" ? "technical" : "business";
    } catch {
      return "business";
    }
  });
  const choose = (v: "business" | "technical") => {
    setView(v);
    try { localStorage.setItem(VIEW_KEY, v); } catch { /* storage unavailable */ }
  };
  const switcher = (
    <div className="seg" role="tablist" aria-label="Map view" data-tour="map-switch">
      <button className={view === "business" ? "on" : ""} onClick={() => choose("business")}><LuLayers size={14} /> Business view (files)</button>
      <button className={view === "technical" ? "on" : ""} onClick={() => choose("technical")}><LuCode size={14} /> Technical view</button>
    </div>
  );
  if (view === "business") {
    return <AppShell title="Dependency map" wide actions={switcher}><BusinessMap projectId={pid} /></AppShell>;
  }
  return <TechnicalGraph pid={pid} switcher={switcher} />;
}

function TechnicalGraph({ pid, switcher }: { pid: number; switcher: ReactNode }) {
  const [types, setTypes] = useState<string[]>(["job", "program", "table", "dataset", "api", "procedure", "external", "unresolved"]);
  const [columns, setColumns] = useState(false);
  const [fks, setFks] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);
  const [depth, setDepth] = useState(2);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [labels, setLabels] = useState(true);
  const canvas = useRef<GraphHandle>(null);
  usePageTour("graph", GRAPH_TOUR);

  const path = `/projects/${pid}/graph${qs({ types: types.join(","), include_columns: columns, include_fk: fks, focus: focus ?? undefined, depth: focus ? depth : undefined })}`;
  const { data, error, loading } = useApi<Graph>(path);
  const graph = useMemo(() => data ?? { nodes: [], edges: [] }, [data]);

  const toggle = (t: string) => setTypes((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]));
  const exportPng = () => {
    const uri = canvas.current?.png();
    if (!uri) return;
    const a = document.createElement("a");
    a.href = uri;
    a.download = `dependency-graph-${pid}.png`;
    a.click();
  };

  return (
    <AppShell title="Dependency map" wide actions={switcher}>
      <div className={`graph-page ${selected ? "" : "no-panel"}`}>
        <div className="graph-stage" data-tour="graph-canvas">
          <div className="graph-toolbar">
            <div className="panel" data-tour="graph-filters">
              {ALL_TYPES.map((t) => (
                <button key={t} className={`chip ${types.includes(t) ? "on" : ""}`} onClick={() => toggle(t)}>
                  <TypeDot type={t} /> {TYPE_LABELS[t]}
                </button>
              ))}
              <label className="check" style={{ padding: "0 6px" }}>
                <input type="checkbox" checked={columns} onChange={(e) => setColumns(e.target.checked)} /> Columns
              </label>
              <label className="check" style={{ padding: "0 6px" }}>
                <input type="checkbox" checked={fks} onChange={(e) => setFks(e.target.checked)} /> Foreign keys
              </label>
            </div>
            <div className="panel" style={{ minWidth: 300 }} data-tour="graph-search">
              <SymbolSearch projectId={pid} types="program,table,job,dataset,api,procedure,copybook,external,column" placeholder="Focus on a program, table, job…"
                value={search} onChange={setSearch} onPick={(h) => {
                  if (h.type === "column") setColumns(true);
                  else if (!types.includes(h.type)) setTypes((cur) => [...cur, h.type]);
                  setFocus(h.id); setSearch(h.name); setSelected(h.id);
                }} />
              {focus && (
                <>
                  <select className="select" style={{ width: 92, height: 34 }} value={depth} onChange={(e) => setDepth(Number(e.target.value))} aria-label="Neighbourhood depth">
                    {[1, 2, 3, 4].map((d) => <option key={d} value={d}>{d} hop{d > 1 ? "s" : ""}</option>)}
                  </select>
                  <button className="btn btn-sm btn-ghost" onClick={() => { setFocus(null); setSearch(""); }}><LuX size={14} /> Clear</button>
                </>
              )}
            </div>
            <div className="spacer" />
            <div className="panel">
              <label className="check" style={{ padding: "0 6px" }}><input type="checkbox" checked={labels} onChange={(e) => setLabels(e.target.checked)} /> Edge labels</label>
              <button className="btn btn-ghost btn-icon btn-sm" title="Zoom in" onClick={() => canvas.current?.zoom(1.25)}><LuZoomIn size={16} /></button>
              <button className="btn btn-ghost btn-icon btn-sm" title="Zoom out" onClick={() => canvas.current?.zoom(0.8)}><LuZoomOut size={16} /></button>
              <button className="btn btn-ghost btn-icon btn-sm" title="Fit to screen" onClick={() => canvas.current?.fit()}><LuMaximize size={16} /></button>
              <button className="btn btn-ghost btn-icon btn-sm" title="Export PNG" onClick={exportPng}><LuImage size={16} /></button>
            </div>
          </div>
          {loading && !data && <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}><Loading label="Laying out graph" /></div>}
          {error && <div style={{ position: "absolute", top: 80, left: 20, right: 20 }}><ErrorBox message={error} /></div>}
          {data && graph.nodes.length === 0 && <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }} className="muted">No elements match the selected filters.</div>}
          {graph.nodes.length > 0 && <GraphCanvas ref={canvas} graph={graph} selected={selected} onSelect={setSelected} showEdgeLabels={labels} dense={graph.nodes.length > 60} />}
          <div className="graph-legend">
            <span><span className="dot" style={{ background: "#15807a" }} /> writes</span>
            <span><span className="dot" style={{ background: "#9aa8ba" }} /> calls / reads / runs</span>
          </div>
          {graph.nodes.length > 0 && <GraphHud graph={graph} />}
        </div>
        {selected && <NodePanel projectId={pid} id={selected} onClose={() => setSelected(null)} onFocus={(id) => { setFocus(id); setSelected(id); }} />}
      </div>
    </AppShell>
  );
}

function NodePanel({ projectId, id, onClose, onFocus }: { projectId: number; id: string; onClose: () => void; onFocus: (id: string) => void }) {
  const { openSource } = useProject();
  const { data, error } = useApi<NodeDetail>(id.startsWith("column:") || id.startsWith("field:") ? null : `/projects/${projectId}/graph/node${qs({ id })}`);
  const label = id.split(":").slice(1).join(":");
  const type = id.split(":")[0];

  const Rel = ({ e, dir }: { e: GraphEdge; dir: "in" | "out" }) => {
    const other = dir === "in" ? e.source : e.target;
    const ev = e.evidence?.[0];
    return (
      <div className="rel">
        <span className="t">{e.type.replace("_", " ").toLowerCase()}</span>
        <TypeDot type={other.split(":")[0]} />
        <button className="btn btn-ghost btn-sm" style={{ height: 24, padding: "0 4px" }} onClick={() => onFocus(other)}>{other.split(":").slice(1).join(":")}</button>
        <span className="spacer" />
        {ev && <button className="cite" onClick={() => openSource(ev.file, ev.line)}>{ev.file.split("/").pop()}:{ev.line}</button>}
      </div>
    );
  };

  return (
    <aside className="side-panel">
      <div className="row" style={{ marginBottom: 14 }}>
        <TypeDot type={type} />
        <span className="subtle">{TYPE_LABELS[type] ?? type}</span>
        <div className="spacer" />
        <button className="btn btn-ghost btn-icon btn-sm" onClick={onClose} aria-label="Close details"><LuX size={16} /></button>
      </div>
      <h2 className="mono" style={{ fontSize: 17, wordBreak: "break-all" }}>{label}</h2>
      {data && (Boolean(data.node.hub) || Boolean(data.node.unused)) && (
        <div className="row-wrap" style={{ marginTop: 8 }}>
          {Boolean(data.node.hub) && <span className="tag tag-red">Critical hub: many parts depend on it</span>}
          {Boolean(data.node.unused) && <span className="tag tag-amber">Unused: nothing calls or runs it</span>}
        </div>
      )}
      {error && <ErrorBox message={error} />}
      {!data && !error && type !== "column" && <Loading />}
      {type === "column" && <p className="muted" style={{ marginTop: 10 }}>Open Data lineage to follow this column field by field.</p>}
      {data && (
        <div className="stack" style={{ marginTop: 12 }}>
          {typeof data.node.purpose === "string" && data.node.purpose && <p className="muted" style={{ fontSize: 14 }}>{data.node.purpose}</p>}
          {data.stats && (
            <div className="metric-tiles">
              <div className="metric-tile"><b>{data.stats.fan_in}</b><span>callers</span></div>
              <div className="metric-tile"><b>{data.stats.fan_out}</b><span>calls out</span></div>
              <div className="metric-tile"><b>{data.stats.business_rules}</b><span>rules</span></div>
            </div>
          )}
          {data.blast_radius && <BlastRadiusCard blast={data.blast_radius} onPick={onFocus} />}
          {data.stats && (
            <dl className="kv">
              <dt>Criticality</dt><dd><span className={`tag ${criticalityTag(data.stats.criticality)}`}>{data.stats.criticality}</span> <span className="subtle">score {data.stats.criticality_score}</span></dd>
              <dt>Type</dt><dd>{data.stats.type}</dd>
              <dt>Size</dt><dd>{data.stats.loc} LOC · complexity {data.stats.complexity}</dd>
              {data.parameters && data.parameters.length > 0 && <><dt>Parameters</dt><dd className="mono" style={{ fontSize: 12.5 }}>{data.parameters.join(", ")}</dd></>}
            </dl>
          )}
          <div className="row-wrap">
            {data.node.file && <button className="btn btn-sm" onClick={() => openSource(data.node.file as string, (data.node.line as number) || 1)}>View source</button>}
            {["program", "table", "copybook", "dataset"].includes(type) && (
              <button className="btn btn-sm btn-primary" onClick={() => navigate(`/projects/${projectId}/impact?target=${encodeURIComponent(id)}`)}>Analyse impact</button>
            )}
          </div>
          {data.incoming.length > 0 && <div><div className="label" style={{ marginBottom: 6 }}>Incoming</div><div className="rel-list">{data.incoming.map((e, i) => <Rel key={i} e={e} dir="in" />)}</div></div>}
          {data.outgoing.length > 0 && <div><div className="label" style={{ marginBottom: 6 }}>Outgoing</div><div className="rel-list">{data.outgoing.map((e, i) => <Rel key={i} e={e} dir="out" />)}</div></div>}
          {data.definition && (
            <div>
              <div className="label" style={{ marginBottom: 6 }}>Columns</div>
              {data.definition.map((c) => (
                <div key={c.name} className="rel"><span className="mono" style={{ fontSize: 12.5 }}>{c.name}</span><span className="spacer" /><span className="subtle">{c.type}{c.primary_key ? " · PK" : ""}</span></div>
              ))}
            </div>
          )}
          {data.paragraphs && (
            <div>
              <div className="label" style={{ marginBottom: 6 }}>Paragraphs</div>
              {data.paragraphs.map((p) => (
                <div key={p.name} className="rel" style={{ cursor: "pointer" }} onClick={() => openSource(data.node.file as string, p.start_line, p.end_line)}>
                  <span className="mono" style={{ fontSize: 12.5 }}>{p.name}</span><span className="spacer" /><span className="subtle">{p.start_line}–{p.end_line}</span>
                </div>
              ))}
            </div>
          )}
          {data.rules && data.rules.length > 0 && (
            <div>
              <div className="label" style={{ marginBottom: 6 }}>Business rules</div>
              {data.rules.slice(0, 8).map((r) => (
                <div key={r.id} className="rel" style={{ alignItems: "flex-start", cursor: "pointer" }} onClick={() => openSource(r.file, r.start_line, r.end_line)}>
                  <span className="mono" style={{ fontSize: 12 }}>{r.id}</span><span style={{ fontSize: 12.5 }}>{r.description}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </aside>
  );
}
