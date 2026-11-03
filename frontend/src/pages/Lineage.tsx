import { useMemo, useState } from "react";
import { AppShell } from "../components/AppShell";
import { GraphCanvas } from "../components/GraphCanvas";
import { useProject } from "../components/ProjectContext";
import { SymbolSearch } from "../components/SymbolSearch";
import { EmptyState, ErrorBox, Loading, TypeDot } from "../components/ui";
import { qs } from "../lib/api";
import { TYPE_LABELS } from "../lib/format";
import { useApi } from "../lib/hooks";
import type { GraphEdge, GraphNode, SymbolHit } from "../lib/types";

type LineageResponse = { root: string; nodes: (GraphNode & { level: number; program?: string })[]; edges: GraphEdge[] };

export function LineagePage() {
  const { project, openSource } = useProject();
  const pid = project!.id;
  const [search, setSearch] = useState("");
  const [node, setNode] = useState<string | null>(null);
  const [direction, setDirection] = useState<"both" | "down" | "up">("both");
  const [depth, setDepth] = useState(4);
  const { data: allExamples } = useApi<SymbolHit[]>(`/projects/${pid}/lineage/examples`);
  const examples = allExamples?.filter((h) => h.type === "column");
  const { data, error, loading } = useApi<LineageResponse>(node ? `/projects/${pid}/lineage${qs({ node, direction, depth })}` : null);

  const rootAccent = useMemo(() => (data ? { [data.root]: "#e9a23b" } : {}), [data]);
  const graph = useMemo(() => {
    if (!data) return null;
    return {
      nodes: data.nodes.map((n) => ({ ...n, label: n.type === "field" ? `${n.program}.${n.label}` : n.label })),
      edges: data.edges.map((e) => ({ ...e, how: (e as GraphEdge & { how?: string }).how ?? e.type.toLowerCase() })),
    };
  }, [data]);

  return (
    <AppShell title="Data lineage">
      <div className="page-head">
        <div>
          <h1>Data lineage</h1>
          <p>Follow a database column or data field through MOVE and COMPUTE statements, SQL, CALL parameters, files and APIs. Dashed amber edges show control dependence: values decided by a condition on the field.</p>
        </div>
      </div>
      <div className="card card-pad" style={{ marginBottom: 16 }}>
        <div className="row">
          <SymbolSearch projectId={pid} types="column,field,dataset" placeholder="Column or field, e.g. INTEREST_RATE, LK-CUST-ADDRESS"
            value={search} onChange={setSearch} onPick={(h) => { setSearch(h.name); setNode(h.id); }} autoFocus />
          <select className="select" style={{ width: 170 }} value={direction} onChange={(e) => setDirection(e.target.value as "both")} aria-label="Direction">
            <option value="both">Upstream and downstream</option>
            <option value="down">Downstream only</option>
            <option value="up">Upstream only</option>
          </select>
          <select className="select" style={{ width: 110 }} value={depth} onChange={(e) => setDepth(Number(e.target.value))} aria-label="Depth">
            {[2, 3, 4, 6, 8].map((d) => <option key={d} value={d}>{d} hops</option>)}
          </select>
        </div>
        <div className="row-wrap" style={{ marginTop: 10 }}>
          <span className="subtle">Try:</span>
          {(examples ?? []).map((h) => (
            <button key={h.id} className="chip" onClick={() => { setNode(h.id); setSearch(h.name); }}>{h.name.split(".").pop()}</button>
          ))}
        </div>
      </div>
      {error && <ErrorBox message={error} />}
      {!node && <div className="card"><EmptyState title="Pick a column or field">Lineage shows where the value comes from and everywhere it goes.</EmptyState></div>}
      {node && loading && !data && <Loading label="Tracing lineage" />}
      {graph && data && (
        <div className="grid-2" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)" }}>
          <div className="card" style={{ overflow: "hidden" }}>
            <div className="graph-stage" style={{ height: 560 }}>
              <GraphCanvas graph={graph} nodeAccent={rootAccent} showEdgeLabels={false} dense />
            </div>
          </div>
          <div className="card">
            <div className="card-head"><h3>Elements ({data.nodes.length})</h3></div>
            <div className="table-wrap" style={{ maxHeight: 510 }}>
              <table className="data">
                <tbody>
                  {[...data.nodes].sort((a, b) => a.level - b.level).map((n) => (
                    <tr key={n.id} className={n.file ? "clickable" : ""} onClick={() => n.file && openSource(n.file as string, (n.line as number) || 1)}>
                      <td><TypeDot type={n.type} /></td>
                      <td className="mono" style={{ fontSize: 12.5 }}>{n.type === "field" ? `${n.program}.${n.label}` : n.label}</td>
                      <td className="subtle">{n.level === 0 ? "selected" : n.level < 0 ? `${-n.level} up` : `${n.level} down`}</td>
                      <td className="subtle">{TYPE_LABELS[n.type]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
