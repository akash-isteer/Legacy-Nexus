import { useMemo, useState } from "react";
import { LuCode, LuDownload, LuLayers, LuSearch } from "react-icons/lu";
import { AppShell } from "../components/AppShell";
import { useProject } from "../components/ProjectContext";
import { usePageTour } from "../components/Tour";
import { RULES_TOUR } from "../components/tours";
import { EmptyState, ErrorBox, Loading, useToast } from "../components/ui";
import { download, qs } from "../lib/api";
import { useApi, useDebounced } from "../lib/hooks";
import type { Rule } from "../lib/types";

type BizRule = Rule & { plain: string; meaning: string; capability: string };
type RulesResponse = { items: BizRule[]; total: number; categories: Record<string, number>; programs: string[] };

const CATEGORY_TAG: Record<string, string> = {
  Validation: "tag-red", Threshold: "tag-amber", Calculation: "tag-blue", Classification: "tag-violet",
  "Domain values": "tag-teal", Decision: "", Technical: "",
};

export function RulesPage() {
  const { project, openSource } = useProject();
  const pid = project!.id;
  const toast = useToast();
  const [view, setView] = useState<"business" | "technical">("business");
  const [category, setCategory] = useState<string | null>(null);
  const [program, setProgram] = useState("");
  const [q, setQ] = useState("");
  const [technical, setTechnical] = useState(false);
  const dq = useDebounced(q, 250);
  usePageTour("rules", RULES_TOUR);

  const { data, error, loading } = useApi<RulesResponse>(
    `/projects/${pid}/rules${qs({ category: category ?? undefined, program: program || undefined, q: dq || undefined, include_technical: technical })}`,
  );

  const totalFound = data ? Object.values(data.categories).reduce((a, b) => a + b, 0) : 0;

  const groups = useMemo(() => {
    const out = new Map<string, BizRule[]>();
    (data?.items ?? []).forEach((r) => out.set(r.capability, [...(out.get(r.capability) ?? []), r]));
    return [...out.entries()];
  }, [data]);

  return (
    <AppShell title="Business rules" actions={
      <div className="seg" role="tablist" aria-label="Rules view">
        <button className={view === "business" ? "on" : ""} onClick={() => setView("business")}><LuLayers size={14} /> Business view</button>
        <button className={view === "technical" ? "on" : ""} onClick={() => setView("technical")}><LuCode size={14} /> Technical view</button>
      </div>
    }>
      <div className="page-head">
        <div>
          <h1>Business rules</h1>
          <p>The policies hidden in the code, found automatically and written in plain words. Every rule links to the exact lines that implement it.</p>
        </div>
        <div className="row-wrap">
          <button className="btn" data-tour="rules-export" onClick={() => download(`/projects/${pid}/rules/export${qs({ include_technical: technical })}`, "business-rules.csv").catch((e) => toast(e.message, true))}>
            <LuDownload size={15} /> Export CSV
          </button>
        </div>
      </div>

      {data && (
        <div className="stat-row" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))", marginBottom: 16 }}>
          <div className="stat"><div className="v">{totalFound}</div><div className="k">Rules found</div></div>
          <div className="stat"><div className="v">{Object.keys(data.categories).length}</div><div className="k">Rule types</div></div>
          <div className="stat"><div className="v">{data.programs.length}</div><div className="k">Programs</div></div>
        </div>
      )}

      <div className="card card-pad" style={{ marginBottom: 16 }} data-tour="rules-filters">
        <div className="row-wrap">
          <button className={`chip ${category === null ? "on" : ""}`} onClick={() => setCategory(null)}>All types</button>
          {Object.entries(data?.categories ?? {}).map(([c, n]) => (
            <button key={c} className={`chip ${category === c ? "on" : ""}`} onClick={() => setCategory(category === c ? null : c)}>{c} <span className="count">{n}</span></button>
          ))}
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <div style={{ position: "relative", flex: 1 }}>
            <LuSearch size={16} style={{ position: "absolute", left: 12, top: 11, color: "var(--text-3)" }} />
            <input className="input" style={{ paddingLeft: 36 }} placeholder="Search rules, e.g. “minimum balance” or “payment”" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <select className="select" style={{ width: 200 }} value={program} onChange={(e) => setProgram(e.target.value)} aria-label="Program">
            <option value="">All programs</option>
            {data?.programs.map((p) => <option key={p}>{p}</option>)}
          </select>
          <label className="check"><input type="checkbox" checked={technical} onChange={(e) => setTechnical(e.target.checked)} /> Include technical checks</label>
        </div>
      </div>

      {error && <ErrorBox message={error} />}
      {loading && !data ? <div className="card"><Loading /></div> : data && data.items.length === 0 ? (
        <div className="card"><EmptyState title="No rules match these filters" /></div>
      ) : view === "business" ? (
        groups.map(([cap, rules]) => (
          <div key={cap}>
            <div className="rule-group-title">{cap}<span className="tag">{rules.length}</span></div>
            <div className="rule-cards">
              {rules.map((r) => (
                <div key={r.id} className="rule-card">
                  <div className="row-wrap" style={{ gap: 8 }}>
                    <span className={`tag ${CATEGORY_TAG[r.category] ?? ""}`}>{r.category}</span>
                    <span className="subtle mono">{r.id}</span>
                    <div className="spacer" />
                    <button className="cite" onClick={() => openSource(r.file, r.start_line, r.end_line)} title="Open the code">{r.file.split("/").pop()}:{r.start_line}</button>
                  </div>
                  <div className="plain">{r.plain}</div>
                  <div className="why">{r.meaning} Found in <b>{r.program}</b>{r.paragraph ? ` (${r.paragraph})` : ""}.</div>
                  <details style={{ marginTop: 8 }}>
                    <summary className="subtle" style={{ cursor: "pointer" }}>Technical detail</summary>
                    <div style={{ fontSize: 13.5, marginTop: 6 }}>{r.description}</div>
                    {r.condition && <div className="mono subtle" style={{ fontSize: 12, marginTop: 4 }}>{r.condition}</div>}
                    {r.fields.length > 0 && <div className="subtle" style={{ marginTop: 4 }}>Data involved: <span className="mono">{r.fields.join(", ")}</span></div>}
                  </details>
                </div>
              ))}
            </div>
          </div>
        ))
      ) : (
        <div className="card">
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th style={{ width: 80 }}>ID</th><th style={{ width: 130 }}>Category</th><th>Rule</th><th style={{ width: 170 }}>Source</th></tr></thead>
              <tbody>
                {data?.items.map((r) => (
                  <tr key={r.id}>
                    <td className="mono" style={{ fontSize: 13 }}>{r.id}</td>
                    <td><span className={`tag ${CATEGORY_TAG[r.category] ?? ""}`}>{r.category}</span></td>
                    <td>
                      <div style={{ fontSize: 14 }}>{r.description}</div>
                      {r.condition && <div className="mono subtle" style={{ fontSize: 12, marginTop: 4 }}>{r.condition}</div>}
                    </td>
                    <td>
                      <button className="cite" onClick={() => openSource(r.file, r.start_line, r.end_line)}>{r.file.split("/").pop()}:{r.start_line}-{r.end_line}</button>
                      <div className="subtle" style={{ marginTop: 4 }}>{r.program}{r.paragraph ? ` · ${r.paragraph}` : ""}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {data && <p className="subtle" style={{ marginTop: 10 }}>{data.total} rule(s) shown</p>}
    </AppShell>
  );
}
