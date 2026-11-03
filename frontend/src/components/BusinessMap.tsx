import { useEffect, useMemo, useRef, useState } from "react";
import { LuCirclePause, LuImage, LuMaximize, LuPlay, LuRadar, LuSearch, LuSkipForward, LuX, LuZoomIn, LuZoomOut } from "react-icons/lu";
import { useApi } from "../lib/hooks";
import { navigate } from "../lib/router";
import type { BlastRadius, Graph } from "../lib/types";
import { GraphCanvas, type GraphHandle } from "./GraphCanvas";
import { BlastRadiusCard } from "./GraphInsights";
import { useProject } from "./ProjectContext";
import { usePageTour } from "./Tour";
import { BUSINESS_MAP_TOUR } from "./tours";
import { ErrorBox, Loading } from "./ui";

type Box = {
  id: string; kind: "file" | "data" | "datafile" | "external" | "unresolved"; label: string; path: string | null; folder: string;
  language: string | null; role: string; capability: string | null; components: string[]; purpose: string;
  business_rules: number; lines: number; criticality: string | null; summary: string;
};
type Link = { source: string; target: string; type: string; verb: string; count: number; sentence: string; evidence: { file: string; line: number }[] };
type FileView = { nodes: Box[]; links: Link[] };

const ROLE_TYPE: Record<string, string> = {
  Program: "program", "Batch job": "job", "Batch script": "job", "Database script": "procedure", "API contract": "api",
  Configuration: "config", "Shared record layout": "copybook", Document: "field", "Database table": "table",
  "Data file / report": "dataset", "Outside system": "external", "Decided at runtime": "unresolved",
};
const LEGEND = [
  ["program", "Program"], ["job", "Batch job / script"], ["table", "Database table"], ["dataset", "Data file / report"],
  ["api", "API"], ["external", "Outside system"], ["unresolved", "Decided at runtime"],
];

/** Blast radius of the main program in a file (the technical graph is keyed by program, not file). */
function FileBlast({ projectId, component, onPick }: { projectId: number; component: string; onPick: (id: string) => void }) {
  const { data } = useApi<{ blast_radius?: BlastRadius }>(`/projects/${projectId}/graph/node?id=${encodeURIComponent(`program:${component}`)}`);
  if (!data?.blast_radius) return null;
  return <div style={{ marginTop: 14 }}><BlastRadiusCard blast={data.blast_radius} onPick={onPick} /></div>;
}

export function BusinessMap({ projectId }: { projectId: number }) {
  const { openSource } = useProject();
  const { data, error, loading } = useApi<FileView>(`/projects/${projectId}/graph/files`);
  const canvas = useRef<GraphHandle>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [capability, setCapability] = useState("");
  const [showData, setShowData] = useState(true);
  const [showOutside, setShowOutside] = useState(true);
  const [showDocs, setShowDocs] = useState(false);
  const [neighbourhood, setNeighbourhood] = useState(false);
  const [story, setStory] = useState<{ steps: Link[]; at: number; playing: boolean } | null>(null);
  usePageTour("business-map", BUSINESS_MAP_TOUR, !!data);

  const boxes = useMemo(() => new Map((data?.nodes ?? []).map((n) => [n.id, n])), [data]);
  const capabilities = useMemo(() => [...new Set((data?.nodes ?? []).map((n) => n.capability).filter(Boolean) as string[])].sort(), [data]);

  const graph: Graph = useMemo(() => {
    if (!data) return { nodes: [], edges: [] };
    const query = q.trim().toLowerCase();
    let visible = data.nodes.filter((n) => {
      if (n.kind === "data" || n.kind === "datafile") return showData;
      if (n.kind === "external" || n.kind === "unresolved") return showOutside;
      if (n.role === "Document") return showDocs;
      return !capability || n.capability === capability;
    });
    if (capability) {  // keep data and outside boxes only when linked to the chosen business area
      const inArea = new Set(visible.filter((n) => n.kind === "file").map((n) => n.id));
      const linked = new Set(data.links.filter((l) => inArea.has(l.source) || inArea.has(l.target)).flatMap((l) => [l.source, l.target]));
      visible = visible.filter((n) => n.kind === "file" || linked.has(n.id));
    }
    if (neighbourhood && selected) {
      const near = new Set([selected, ...data.links.filter((l) => l.source === selected || l.target === selected).flatMap((l) => [l.source, l.target])]);
      visible = visible.filter((n) => near.has(n.id));
    }
    if (query) {
      const hits = new Set(visible.filter((n) => `${n.label} ${n.path ?? ""} ${n.components.join(" ")} ${n.capability ?? ""}`.toLowerCase().includes(query)).map((n) => n.id));
      const near = new Set([...hits, ...data.links.filter((l) => hits.has(l.source) || hits.has(l.target)).flatMap((l) => [l.source, l.target])]);
      visible = visible.filter((n) => near.has(n.id));
    }
    const ids = new Set(visible.map((n) => n.id));
    return {
      nodes: visible.map((n) => ({
        id: n.id, label: n.label, type: ROLE_TYPE[n.role] ?? "external",
        criticality: n.criticality ?? undefined, hub: n.criticality === "Critical", unused: n.criticality === "Unused",
      })),
      edges: data.links.filter((l) => ids.has(l.source) && ids.has(l.target)).map((l) => ({ source: l.source, target: l.target, type: l.type, how: l.verb })),
    };
  }, [data, q, capability, showData, showOutside, showDocs, neighbourhood, selected]);

  // Follow the flow: downstream links from the selected box, in the order work and data move.
  const startStory = () => {
    if (!data || !selected) return;
    const steps: Link[] = [];
    const seen = new Set([selected]);
    const queue = [selected];
    while (queue.length && steps.length < 30) {
      const cur = queue.shift()!;
      for (const l of data.links.filter((x) => x.source === cur && x.type !== "REFERENCES" && x.type !== "DEFINES")) {
        steps.push(l);
        if (!seen.has(l.target)) {
          seen.add(l.target);
          queue.push(l.target);
        }
      }
    }
    setStory({ steps, at: 0, playing: steps.length > 0 });
  };

  useEffect(() => {
    if (!story?.playing) return;
    if (story.at >= story.steps.length - 1) {
      setStory((s) => (s ? { ...s, playing: false } : s));
      return;
    }
    const t = setTimeout(() => setStory((s) => (s ? { ...s, at: s.at + 1 } : s)), 1800);
    return () => clearTimeout(t);
  }, [story]);

  useEffect(() => {
    const step = story?.steps[story.at];
    if (step) canvas.current?.focus(step.target);
  }, [story?.at, story?.steps]);

  useEffect(() => setStory(null), [selected]);

  const accent = useMemo(() => {
    const a: Record<string, string> = {};
    if (!story) return a;
    story.steps.slice(0, story.at + 1).forEach((l, i) => {
      a[l.source] = a[l.source] ?? "#2a8253";
      a[l.target] = i === story.at ? "#e9a23b" : "#2a8253";
    });
    return a;
  }, [story]);

  const exportPng = () => {
    const uri = canvas.current?.png();
    if (!uri) return;
    const a = document.createElement("a");
    a.href = uri;
    a.download = `business-map-${projectId}.png`;
    a.click();
  };

  const box = selected ? boxes.get(selected) : undefined;
  const outgoing = data && selected ? data.links.filter((l) => l.source === selected) : [];
  const incoming = data && selected ? data.links.filter((l) => l.target === selected) : [];

  return (
    <div className={`graph-page ${selected ? "" : "no-panel"}`}>
      <div className="graph-stage" data-tour="map-canvas">
        <div className="graph-toolbar">
          <div className="panel" style={{ minWidth: 260, flexWrap: "nowrap" }} data-tour="map-search">
            <LuSearch size={15} style={{ marginLeft: 6, color: "var(--text-3)" }} />
            <input className="input" style={{ border: "none", height: 30, boxShadow: "none" }} placeholder="Find a file, table or business area" value={q} onChange={(e) => setQ(e.target.value)} />
            {q && <button className="btn btn-ghost btn-icon btn-sm" onClick={() => setQ("")} aria-label="Clear search"><LuX size={14} /></button>}
          </div>
          <div className="panel" data-tour="map-filters">
            <select className="select" style={{ height: 32, width: 210 }} value={capability} onChange={(e) => setCapability(e.target.value)} aria-label="Business area">
              <option value="">All business areas</option>
              {capabilities.map((c) => <option key={c}>{c}</option>)}
            </select>
            <label className="check" style={{ padding: "0 6px" }}><input type="checkbox" checked={showData} onChange={(e) => setShowData(e.target.checked)} /> Data</label>
            <label className="check" style={{ padding: "0 6px" }}><input type="checkbox" checked={showOutside} onChange={(e) => setShowOutside(e.target.checked)} /> Outside systems</label>
            <label className="check" style={{ padding: "0 6px" }}><input type="checkbox" checked={showDocs} onChange={(e) => setShowDocs(e.target.checked)} /> Documents</label>
          </div>
          <div className="spacer" />
          <div className="panel">
            <button className="btn btn-ghost btn-icon btn-sm" title="Zoom in" onClick={() => canvas.current?.zoom(1.25)}><LuZoomIn size={16} /></button>
            <button className="btn btn-ghost btn-icon btn-sm" title="Zoom out" onClick={() => canvas.current?.zoom(0.8)}><LuZoomOut size={16} /></button>
            <button className="btn btn-ghost btn-icon btn-sm" title="Fit to screen" onClick={() => canvas.current?.fit()}><LuMaximize size={16} /></button>
            <button className="btn btn-ghost btn-icon btn-sm" title="Export PNG" onClick={exportPng}><LuImage size={16} /></button>
          </div>
        </div>
        {loading && !data && <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}><Loading label="Building the map" /></div>}
        {error && <div style={{ position: "absolute", top: 80, left: 20, right: 20 }}><ErrorBox message={error} /></div>}
        {data && graph.nodes.length === 0 && <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }} className="muted">Nothing matches these filters.</div>}
        {graph.nodes.length > 0 && <GraphCanvas ref={canvas} graph={graph} selected={selected} onSelect={setSelected} nodeAccent={accent} showEdgeLabels dense={graph.nodes.length > 50} />}
        <div className="graph-legend">
          <span>
            {graph.nodes.filter((n) => n.id.startsWith("file:")).length} files · {graph.edges.length} connections
            {" · "}<b style={{ color: "var(--red)" }}>{graph.nodes.filter((n) => n.hub).length}</b> critical
            {" · "}<b style={{ color: "var(--amber-ink)" }}>{graph.nodes.filter((n) => n.unused).length}</b> unused
          </span>
          {LEGEND.map(([t, label]) => <span key={t}><span className="dot" style={{ background: `var(--t-${t})` }} /> {label}</span>)}
          <span className="subtle">Click any box to see what it does in plain words.</span>
        </div>
      </div>

      {box && (
        <aside className="side-panel">
          <div className="row" style={{ marginBottom: 10 }}>
            <span className="dot" style={{ background: `var(--t-${ROLE_TYPE[box.role] ?? "external"})` }} />
            <span className="subtle">{box.role}</span>
            {box.capability && <span className="cap-chip">{box.capability}</span>}
            <div className="spacer" />
            <button className="btn btn-ghost btn-icon btn-sm" onClick={() => setSelected(null)} aria-label="Close"><LuX size={16} /></button>
          </div>
          <h2 style={{ fontSize: 18, wordBreak: "break-all" }}>{box.label}</h2>
          {box.path && <div className="subtle mono" style={{ fontSize: 12, marginTop: 2, wordBreak: "break-all" }}>{box.path}</div>}
          <div className="explain-box" style={{ marginTop: 12 }}>{box.summary}</div>
          {box.components[0] && box.kind === "file" && <FileBlast projectId={projectId} component={box.components[0]} onPick={(id) => {
            const hit = data?.nodes.find((n) => n.components.includes(id.split(":").slice(1).join(":")) || n.label === id.split(":").slice(1).join(":"));
            if (hit) setSelected(hit.id);
          }} />}
          {box.kind === "file" && (
            <dl className="kv" style={{ marginTop: 14 }}>
              {box.criticality && <><dt>Importance</dt><dd><span className={`tag ${box.criticality === "Critical" ? "tag-red" : box.criticality === "High" ? "tag-amber" : box.criticality === "Unused" ? "" : "tag-blue"}`}>{box.criticality}</span></dd></>}
              <dt>Business rules</dt><dd>{box.business_rules}</dd>
              <dt>Size</dt><dd>{box.lines} lines</dd>
              {box.components.length > 0 && <><dt>Contains</dt><dd className="mono" style={{ fontSize: 12.5 }}>{box.components.join(", ")}</dd></>}
            </dl>
          )}
          <div className="row-wrap" style={{ marginTop: 14 }}>
            <button className="btn btn-sm btn-signal" onClick={startStory} disabled={!outgoing.length}><LuPlay size={14} /> Follow the flow</button>
            {box.path && <button className="btn btn-sm" onClick={() => openSource(box.path as string, 1)}>View source</button>}
            {box.components[0] && <button className="btn btn-sm" onClick={() => navigate(`/projects/${projectId}/impact?target=${encodeURIComponent(`program:${box.components[0]}`)}`)}><LuRadar size={14} /> What breaks if this changes?</button>}
            <label className="check" style={{ fontSize: 13 }}><input type="checkbox" checked={neighbourhood} onChange={(e) => setNeighbourhood(e.target.checked)} /> Only show its connections</label>
          </div>

          {story && (
            <div className="story">
              <div className="row" style={{ marginBottom: 8 }}>
                <b style={{ fontSize: 14 }}>What happens next</b>
                <span className="subtle">step {Math.min(story.at + 1, story.steps.length)} of {story.steps.length}</span>
                <div className="spacer" />
                <button className="btn btn-ghost btn-icon btn-sm" onClick={() => setStory({ ...story, playing: !story.playing })} aria-label={story.playing ? "Pause" : "Play"}>{story.playing ? <LuCirclePause size={16} /> : <LuPlay size={16} />}</button>
                <button className="btn btn-ghost btn-icon btn-sm" onClick={() => setStory({ ...story, at: Math.min(story.at + 1, story.steps.length - 1), playing: false })} aria-label="Next step"><LuSkipForward size={16} /></button>
              </div>
              {story.steps.map((l, i) => (
                <div key={i} className={`story-step ${i === story.at ? "now" : i < story.at ? "done" : ""}`} onClick={() => setStory({ ...story, at: i, playing: false })}>
                  <span className="n">{i + 1}</span><span>{l.sentence}</span>
                </div>
              ))}
            </div>
          )}

          {!story && (outgoing.length > 0 || incoming.length > 0) && (
            <div className="stack-sm" style={{ marginTop: 16 }}>
              <div className="label">How it connects</div>
              {[...outgoing, ...incoming].map((l, i) => (
                <div key={i} className="rel" style={{ alignItems: "flex-start" }}>
                  <span style={{ fontSize: 13, cursor: "pointer" }} onClick={() => setSelected(l.source === box.id ? l.target : l.source)}>{l.sentence}</span>
                  <span className="spacer" />
                  {l.evidence[0] && <button className="cite" onClick={() => openSource(l.evidence[0].file, l.evidence[0].line)}>{l.evidence[0].file.split("/").pop()}:{l.evidence[0].line}</button>}
                </div>
              ))}
            </div>
          )}
        </aside>
      )}
    </div>
  );
}
