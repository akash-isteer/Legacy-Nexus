import { useEffect, useState } from "react";
import { LuRadar } from "react-icons/lu";
import { TYPE_LABELS } from "../lib/format";
import type { BlastRadius, Graph } from "../lib/types";

const BAR_COLORS: Record<string, string> = {
  program: "#2c67a8", job: "#6352c6", api: "#ad3f78", table: "#15807a", dataset: "#a8701a", procedure: "#3b8a43", external: "#6f7c8e",
};

/** Ring gauge + per-type bars: what may need re-testing if this component changes. */
export function BlastRadiusCard({ blast, onPick }: { blast: BlastRadius; onPick?: (id: string) => void }) {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    setShown(0);
    const t = setTimeout(() => setShown(blast.total), 60);
    return () => clearTimeout(t);
  }, [blast.total]);
  const r = 31;
  const c = 2 * Math.PI * r;
  // Log scale so 3 and 300 both read sensibly on the ring.
  const pct = Math.min(1, Math.log10(1 + shown) / Math.log10(1 + Math.max(25, blast.total)));
  const calm = blast.total === 0;
  const color = blast.total >= 20 ? "#ef4444" : blast.total >= 6 ? "#f59e0b" : calm ? "#22c55e" : "#3b82f6";
  const rows = Object.entries(blast.counts).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...rows.map(([, n]) => n));
  return (
    <div className={`blast ${calm ? "calm" : ""}`}>
      <div className="blast-top">
        <div className="blast-ring">
          <svg width="74" height="74" viewBox="0 0 74 74">
            <circle cx="37" cy="37" r={r} fill="none" stroke="var(--line)" strokeWidth="7" />
            <circle className="val" cx="37" cy="37" r={r} fill="none" stroke={color} strokeWidth="7" strokeLinecap="round"
              strokeDasharray={c} strokeDashoffset={c * (1 - (calm ? 1 : pct))} />
          </svg>
          <div className="num" style={{ color }}>{blast.total}</div>
        </div>
        <div>
          <h4><LuRadar size={14} style={{ verticalAlign: -2 }} /> Blast radius</h4>
          <p>{calm ? "Nothing else depends on this. Changes stay local." :
            `${blast.total} component${blast.total === 1 ? "" : "s"} may need re-testing if this changes, up to ${blast.max_depth} step${blast.max_depth === 1 ? "" : "s"} away.`}</p>
        </div>
      </div>
      {rows.length > 0 && (
        <div className="blast-bars">
          {rows.map(([t, n]) => (
            <div key={t} className="blast-bar">
              <span>{TYPE_LABELS[t] ?? t}</span>
              <div className="track"><span style={{ width: `${(n / max) * 100}%`, background: BAR_COLORS[t] ?? "#64748b" }} /></div>
              <b>{n}</b>
            </div>
          ))}
        </div>
      )}
      {onPick && blast.sample.length > 0 && (
        <div className="row-wrap" style={{ gap: 4, marginTop: 10 }}>
          {blast.sample.slice(0, 8).map((s) => (
            <button key={s.id} className="chip" style={{ height: 24, fontSize: 11.5 }} onClick={() => onPick(s.id)} title={`${s.hops} step(s) away`}>
              {s.id.split(":").slice(1).join(":")}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Live counters for what is on the canvas. */
export function GraphHud({ graph, unit = "elements" }: { graph: Graph; unit?: string }) {
  const hubs = graph.nodes.filter((n) => n.hub).length;
  const dead = graph.nodes.filter((n) => n.unused).length;
  return (
    <div className="graph-hud" aria-label="Map summary">
      <div className="m"><b>{graph.nodes.length}</b><span>{unit}</span></div>
      <div className="m"><b>{graph.edges.length}</b><span>links</span></div>
      <div className="m hub"><b>{hubs}</b><span>hubs</span></div>
      <div className="m dead"><b>{dead}</b><span>unused</span></div>
    </div>
  );
}
