import { useState } from "react";
import { LuDownload, LuRocket, LuTriangleAlert } from "react-icons/lu";
import { AppShell } from "../components/AppShell";
import { useProject } from "../components/ProjectContext";
import { usePageTour } from "../components/Tour";
import { MODERNIZE_TOUR } from "../components/tours";
import { ErrorBox, useToast } from "../components/ui";
import { api, download, qs } from "../lib/api";
import { criticalityTag, severityTag } from "../lib/format";

type Plan = {
  targets: { language: string; database: string; platform: string };
  readiness: { score: number; level: string; factors: { name: string; score: number; detail: string }[] };
  assessments: { program: string; file: string; type: string; loc: number; complexity: number; criticality: string; fan_in: number; business_rules: number; strategy: string; rationale: string; risk: string; target_component: string }[];
  services: { name: string; capability: string; technology: string; programs: string[]; owned_tables: string[]; endpoints: string[]; batch_jobs: string[]; hosting: string }[];
  architecture: { layers: { name: string; components: string[] }[]; mappings: { legacy: string; target: string }[] };
  waves: { wave: number; name: string; programs: string[]; tables?: string[]; activities: string[]; risk: string; rules_to_verify: number }[];
  ddl: { table: string; source: string; target: string; status: string; file: string }[];
  risks: { severity: string; title: string; detail: string }[];
  parity_summary: { scenarios: number; rules_covered: number };
  strategy_counts: Record<string, number>;
};

const OPTIONS = {
  language: [["java", "Java + Spring Boot"], ["dotnet", ".NET + ASP.NET Core"], ["python", "Python + FastAPI"]],
  database: [["postgresql", "PostgreSQL"], ["azure_sql", "Azure SQL"], ["oracle", "Oracle"], ["mysql", "MySQL"]],
  platform: [["azure", "Microsoft Azure"], ["aws", "AWS"], ["gcp", "Google Cloud"], ["on_prem", "On-prem Kubernetes"]],
} as const;

const STRATEGY_TAG: Record<string, string> = { Retire: "", Rehost: "tag-teal", Replatform: "tag-blue", Refactor: "tag-violet", Encapsulate: "tag-amber" };

export function ModernizePage() {
  const { project, openSource } = useProject();
  const pid = project!.id;
  const toast = useToast();
  const [targets, setTargets] = useState({ language: "java", database: "postgresql", platform: "azure" });
  const [plan, setPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  usePageTour("modernize", MODERNIZE_TOUR);

  const generate = async () => {
    setBusy(true);
    setError(null);
    try {
      setPlan(await api.post<Plan>(`/projects/${pid}/modernization`, targets));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const dl = (format: string, name: string) => download(`/projects/${pid}/modernization/parity${qs({ format })}`, name).catch((e) => toast(e.message, true));

  return (
    <AppShell title="Modernization">
      <div className="page-head">
        <div>
          <h1>Modernization plan</h1>
          <p>Choose the target stack. The assessment, strategy, waves and architecture are computed from the knowledge graph and the extracted rules.</p>
        </div>
      </div>
      <div className="card card-pad" style={{ marginBottom: 20 }}>
        <div className="row-wrap" style={{ alignItems: "flex-end", gap: 16 }}>
          <div className="row-wrap" data-tour="mod-targets" style={{ gap: 16 }}>
            {(Object.keys(OPTIONS) as (keyof typeof OPTIONS)[]).map((k) => (
              <div className="field" key={k} style={{ width: 210 }}>
                <label htmlFor={k}>{k === "language" ? "Target language" : k === "database" ? "Target database" : "Target platform"}</label>
                <select id={k} className="select" value={targets[k]} onChange={(e) => setTargets({ ...targets, [k]: e.target.value })}>
                  {OPTIONS[k].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </div>
            ))}
          </div>
          <button className="btn btn-primary" onClick={generate} disabled={busy} data-tour="mod-generate">
            {busy ? <span className="spinner" /> : <LuRocket size={16} />} Generate plan
          </button>
        </div>
      </div>
      {error && <ErrorBox message={error} />}
      {!plan && !error && <div className="info-box">Pick the targets and generate the plan. It takes about a second and uses no AI tokens.</div>}

      {plan && (
        <div className="stack" style={{ gap: 20 }}>
          <div className="grid-2">
            <div className="card card-pad">
              <div className="gauge">
                <Gauge value={plan.readiness.score} />
                <div>
                  <h2>{plan.readiness.level}</h2>
                  <p className="muted">Readiness for {plan.targets.language} on {plan.targets.platform} with {plan.targets.database}.</p>
                </div>
              </div>
              <div className="stack-sm" style={{ marginTop: 18 }}>
                {plan.readiness.factors.map((f) => (
                  <div key={f.name}>
                    <div className="row" style={{ fontSize: 13.5 }}><span>{f.name}</span><span className="spacer" /><b>{f.score}</b></div>
                    <div className="bar" style={{ margin: "4px 0" }}><span style={{ width: `${f.score}%`, background: f.score >= 75 ? "var(--green)" : f.score >= 50 ? "var(--amber)" : "var(--red)" }} /></div>
                    <div className="subtle">{f.detail}</div>
                  </div>
                ))}
              </div>
            </div>
            <div className="card card-pad">
              <h3 style={{ marginBottom: 12 }}>Strategy mix (6R)</h3>
              <div className="row-wrap">
                {Object.entries(plan.strategy_counts).map(([k, v]) => <span key={k} className={`tag ${STRATEGY_TAG[k]}`} style={{ height: 28, fontSize: 13 }}>{k} · {v}</span>)}
              </div>
              <h3 style={{ margin: "22px 0 10px" }}>Behavioural parity pack</h3>
              <p className="muted" style={{ fontSize: 14 }}>
                {plan.parity_summary.scenarios} test scenarios covering {plan.parity_summary.rules_covered} business rules: positive and negative cases, each traced to its source lines.
                Run them against the legacy and the new system and compare.
              </p>
              <div className="row" style={{ marginTop: 12 }}>
                <button className="btn btn-sm" onClick={() => dl("csv", "parity-tests.csv")}><LuDownload size={14} /> CSV</button>
                <button className="btn btn-sm" onClick={() => dl("gherkin", "parity.feature")}><LuDownload size={14} /> Gherkin (.feature)</button>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-head"><h3>Migration waves</h3><span className="subtle">Foundation capabilities first, strangler pattern</span></div>
            <div className="card-body waves">
              {plan.waves.map((w) => (
                <div className="wave" key={w.wave}>
                  <div className="rail"><span className="n">{w.wave}</span><span className="l" /></div>
                  <div className="body">
                    <div className="row-wrap"><b>{w.name}</b><span className={`tag ${severityTag(w.risk)}`}>{w.risk} risk</span>{w.rules_to_verify > 0 && <span className="tag">{w.rules_to_verify} rules to verify</span>}</div>
                    <div className="subtle mono" style={{ margin: "4px 0 6px" }}>{w.programs.join(", ")}{w.tables && w.tables.length ? ` · ${w.tables.join(", ")}` : ""}</div>
                    <ul className="muted" style={{ margin: 0, paddingLeft: 18, fontSize: 13.5 }}>{w.activities.map((a) => <li key={a}>{a}</li>)}</ul>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="grid-2">
            <div className="card">
              <div className="card-head"><h3>Target architecture</h3></div>
              <div className="card-body layers">
                {plan.architecture.layers.map((l) => (
                  <div key={l.name} className={`layer ${l.name === "Services" ? "svc" : l.name === "Data" ? "data" : ""}`}>
                    <span className="name">{l.name}</span>
                    <div className="boxes">{l.components.map((c) => <span key={c} className="box">{c}</span>)}</div>
                  </div>
                ))}
              </div>
            </div>
            <div className="card">
              <div className="card-head"><h3>Legacy to target mapping</h3></div>
              <div className="table-wrap">
                <table className="data"><tbody>
                  {plan.architecture.mappings.map((m) => <tr key={m.legacy}><td className="subtle">{m.legacy}</td><td>{m.target}</td></tr>)}
                </tbody></table>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-head"><h3>Proposed services</h3></div>
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Service</th><th>Replaces</th><th>Owns data</th><th>Endpoints</th></tr></thead>
                <tbody>
                  {plan.services.map((s) => (
                    <tr key={s.name}>
                      <td><b className="mono" style={{ fontSize: 13 }}>{s.name}</b><div className="subtle">{s.capability} · {s.technology}</div></td>
                      <td className="mono" style={{ fontSize: 13 }}>{s.programs.join(", ")}</td>
                      <td className="mono" style={{ fontSize: 13 }}>{s.owned_tables.join(", ") || "—"}</td>
                      <td style={{ fontSize: 13 }}>{s.endpoints.length ? s.endpoints.map((e) => <div key={e} className="mono" style={{ fontSize: 12.5 }}>{e}</div>) : <span className="subtle">batch only{s.batch_jobs.length ? ` (${s.batch_jobs.join(", ")})` : ""}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card">
            <div className="card-head"><h3>Program assessment</h3></div>
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Program</th><th>Criticality</th><th className="num">LOC</th><th className="num">Complexity</th><th className="num">Rules</th><th>Strategy</th><th>Risk</th><th>Target</th></tr></thead>
                <tbody>
                  {plan.assessments.map((a) => (
                    <tr key={a.program} className="clickable" onClick={() => openSource(a.file, 1)}>
                      <td><b className="mono" style={{ fontSize: 13 }}>{a.program}</b><div className="subtle">{a.type}</div></td>
                      <td><span className={`tag ${criticalityTag(a.criticality)}`}>{a.criticality}</span></td>
                      <td className="num">{a.loc}</td><td className="num">{a.complexity}</td><td className="num">{a.business_rules}</td>
                      <td><span className={`tag ${STRATEGY_TAG[a.strategy]}`} title={a.rationale}>{a.strategy}</span><div className="subtle" style={{ maxWidth: 260, marginTop: 4 }}>{a.rationale}</div></td>
                      <td><span className={`tag ${severityTag(a.risk)}`}>{a.risk}</span></td>
                      <td style={{ fontSize: 13 }}>{a.target_component}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card">
            <div className="card-head"><h3>Schema conversion</h3><span className="subtle">DB2 DDL transpiled with sqlglot · review before use</span></div>
            <div className="card-body stack">
              {plan.ddl.length === 0 && <p className="muted">No DDL was uploaded.</p>}
              {plan.ddl.map((d) => (
                <div key={d.table}>
                  <div className="row" style={{ marginBottom: 6 }}><b className="mono">{d.table}</b><span className={`tag ${d.status === "converted" ? "tag-green" : "tag-amber"}`}>{d.status}</span></div>
                  <div className="ddl-pair"><pre>{d.source}</pre><pre>{d.target}</pre></div>
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="card-head"><h3 className="row"><LuTriangleAlert size={17} color="var(--amber-strong)" /> Risks to address</h3></div>
            <div className="table-wrap">
              <table className="data"><tbody>
                {plan.risks.length === 0 && <tr><td className="subtle">No specific risks were detected.</td></tr>}
                {plan.risks.map((r, i) => (
                  <tr key={i}><td style={{ width: 100 }}><span className={`tag ${severityTag(r.severity)}`}>{r.severity}</span></td><td><b>{r.title}</b><div className="subtle">{r.detail}</div></td></tr>
                ))}
              </tbody></table>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}

function Gauge({ value }: { value: number }) {
  const r = 44, c = Math.PI * r;
  const color = value >= 75 ? "var(--green)" : value >= 50 ? "var(--amber-strong)" : "var(--red)";
  return (
    <svg width="120" height="70" viewBox="0 0 120 70" aria-label={`Readiness ${value}`}>
      <path d="M16 62 A44 44 0 0 1 104 62" fill="none" stroke="var(--line)" strokeWidth="10" strokeLinecap="round" />
      <path d="M16 62 A44 44 0 0 1 104 62" fill="none" stroke={color} strokeWidth="10" strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - value / 100)} />
      <text x="60" y="60" textAnchor="middle" fontSize="24" fontWeight="600" fill="var(--text)">{value}</text>
    </svg>
  );
}
