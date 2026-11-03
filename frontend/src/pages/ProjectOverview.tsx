import { useState } from "react";
import {
  LuArchive, LuCalendarClock, LuCode, LuDatabase, LuFileCode2, LuFiles, LuFolderGit2, LuGitFork, LuMessageSquareText,
  LuPlay, LuPlug, LuRefreshCw, LuRocket, LuScale, LuShieldAlert, LuTimer, LuTriangleAlert, LuUpload,
} from "react-icons/lu";
import { AppShell } from "../components/AppShell";
import { ApplicationMapCard, CodeCard, InventoryCard, ProgramsCard, RepositoryCard, type RepoFile } from "../components/DashboardPanels";
import { ChangesStrip, DataHotspotsCard, SensitiveDataCard } from "../components/OverviewExtras";
import { useProject } from "../components/ProjectContext";
import { usePageTour } from "../components/Tour";
import { OVERVIEW_TOUR } from "../components/tours";
import { ErrorBox, Loading, useToast } from "../components/ui";
import { api } from "../lib/api";
import { fmtDate, fmtNumber } from "../lib/format";
import { useApi } from "../lib/hooks";
import { navigate } from "../lib/router";
import type { OverviewResponse } from "../lib/types";
import { StatusCell } from "./Projects";

type Step = { path: string; icon: typeof LuGitFork; title: string; text: string; urgent?: boolean };

/** Up to four actions chosen from this application's own results, most useful first. */
function nextSteps(o: Record<string, number>): Step[] {
  const steps: Step[] = [];
  if (o.api_breaking_changes)
    steps.push({ path: "graph", icon: LuTriangleAlert, urgent: true, title: `${o.api_breaking_changes} API breaking change(s)`,
                 text: "An endpoint was removed or now needs new input since the last analysis. Check callers before release." });
  if (o.business_rules)
    steps.push({ path: "rules", icon: LuScale, title: `Review ${o.business_rules} business rules`,
                 text: "Read the policies found in the code in plain words, and confirm or reject each one." });
  steps.push({ path: "graph", icon: LuGitFork, title: "See how the files connect",
               text: "A plain-language map of every file, table and outside system. Click a file and press “Follow the flow”." });
  if (o.unresolved_calls)
    steps.push({ path: "graph", icon: LuTriangleAlert, urgent: true, title: `${o.unresolved_calls} call(s) decided at runtime`,
                 text: "The code chooses the target while running. Confirm which one is used before you change it." });
  if (o.unused_components)
    steps.push({ path: "modernize", icon: LuRocket, title: `${o.unused_components} retirement candidate(s)`,
                 text: "Components nothing uses. Removing them first makes modernization cheaper." });
  steps.push({ path: "ask", icon: LuMessageSquareText, title: "Ask a question",
               text: "For example: “Where is interest calculated?” Answers cite the exact lines." });
  return steps.slice(0, 4);
}

export function ProjectOverviewPage() {
  const { project, reload } = useProject();
  if (!project) return <AppShell><Loading /></AppShell>;
  if (project.status !== "ready") return <AppShell title="Dashboard"><AnalysisStatus onChanged={reload} /></AppShell>;
  return <ReadyOverview />;
}

function AnalysisStatus({ onChanged }: { onChanged: () => void }) {
  const { project } = useProject();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  if (!project) return null;

  const run = async () => {
    setBusy(true);
    try {
      await api.post(`/projects/${project.id}/analyze`);
      onChanged();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f, f.name));
    try {
      await api.upload(`/projects/${project.id}/files`, form);
      toast(`${files.length} file(s) uploaded`);
      onChanged();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  return (
    <div className="card card-pad" style={{ maxWidth: 720 }}>
      <h2 style={{ marginBottom: 8 }}>{project.status === "analyzing" ? "Analysing the application" : project.status === "failed" ? "The analysis did not finish" : "Ready to analyse"}</h2>
      <p className="muted" style={{ marginBottom: 18 }}>
        {project.status === "analyzing"
          ? "Parsing sources and building the knowledge graph. This page updates by itself."
          : project.status === "failed"
            ? "Fix the problem below, then run the analysis again."
            : "Upload sources if you have not yet, then run the analysis."}
      </p>
      <StatusCell p={project} />
      {project.error && <div style={{ marginTop: 14 }}><ErrorBox message={project.error} /></div>}
      {project.status !== "analyzing" && (
        <div className="row" style={{ marginTop: 20 }}>
          <label className="btn">
            <LuUpload size={15} /> Upload more files
            <input type="file" multiple hidden onChange={(e) => upload(e.target.files)} />
          </label>
          <button className="btn btn-primary" onClick={run} disabled={busy}>
            {busy ? <span className="spinner" /> : <LuPlay size={15} />} Run analysis
          </button>
        </div>
      )}
    </div>
  );
}

function ReadyOverview() {
  const { project, openSource } = useProject();
  const toast = useToast();
  const { data, error, loading } = useApi<OverviewResponse>(project ? `/projects/${project.id}/overview` : null, [project?.analyzed_at]);
  const { data: files } = useApi<RepoFile[]>(project ? `/projects/${project.id}/files` : null, [project?.analyzed_at]);
  usePageTour("overview", OVERVIEW_TOUR, !!data);
  const [rerun, setRerun] = useState(false);
  if (!project) return null;
  const base = `/projects/${project.id}`;

  const reanalyse = async () => {
    setRerun(true);
    try {
      await api.post(`${base}/analyze`);
      toast("Re-analysis started");
      window.location.reload();
    } catch (e) {
      toast((e as Error).message, true);
      setRerun(false);
    }
  };

  const o = data?.overview;
  const undocumented = data ? data.external_interfaces.filter((x) => !x.documented).length : 0;

  return (
    <AppShell title="Dashboard" actions={<button className="btn btn-sm" onClick={reanalyse} disabled={rerun}><LuRefreshCw size={14} /> Re-run analysis</button>}>
      {loading && !data && <Loading label="Loading dashboard" />}
      {error && <ErrorBox message={error} />}
      {data && o && (
        <div className="stack" style={{ gap: 22 }}>
          <section className="dash-hero">
            <div className="dash-hero-main">
              <span className="dash-hero-ico"><LuFolderGit2 size={24} /></span>
              <div style={{ minWidth: 0 }}>
                <div className="dash-eyebrow">Application dashboard</div>
                <h1>{project.name}</h1>
                <p>{project.description || "Legacy application analysed by LegacyNexus."}</p>
                <div className="dash-meta">
                  <span><LuFiles size={14} /> {fmtNumber(o.files)} files</span>
                  <span><LuCode size={14} /> {fmtNumber(o.lines_of_code)} lines of code</span>
                  <span><LuCalendarClock size={14} /> Analysed {fmtDate(project.analyzed_at)}</span>
                  <span><LuTimer size={14} /> in {o.analysis_seconds}s</span>
                </div>
                <div className="row-wrap" style={{ gap: 6, marginTop: 12 }}>
                  {Object.entries(o.languages).map(([lang, n]) => <span key={lang} className="tag">{lang} · {n}</span>)}
                </div>
              </div>
            </div>
            <div className="dash-hero-actions">
              <button className="btn btn-primary" onClick={() => navigate(`${base}/graph`)}><LuGitFork size={15} /> Open dependency map</button>
              <button className="btn" onClick={() => navigate(`${base}/ask`)}><LuMessageSquareText size={15} /> Ask the code</button>
            </div>
          </section>

          {data.changes && <ChangesStrip changes={data.changes} />}

          <div className="kpi-row" data-tour="stats">
            <Kpi icon={LuFileCode2} tone="blue" k="Programs analysed" v={o.programs_analysed} hint={`${fmtNumber(o.lines_of_code)} lines of code`} />
            <Kpi icon={LuShieldAlert} tone="red" k="Critical programs" v={o.critical_programs} hint={`${pct(o.critical_programs, o.programs_analysed)}% of all programs`} />
            <Kpi icon={LuArchive} tone="amber" k="Unused / low-use" v={o.unused_components} hint={data.decommission?.lines ? `${fmtNumber(data.decommission.lines)} lines could be retired` : "Retirement candidates"} />
            <Kpi icon={LuDatabase} tone="teal" k="DB dependencies" v={o.db_dependencies} hint={`${fmtNumber(o.tables)} tables · ${fmtNumber(o.procedures)} procedures`} />
            <Kpi icon={LuScale} tone="violet" k="Business rules" v={o.business_rules} hint={o.technical_rules ? `+ ${fmtNumber(o.technical_rules)} technical checks` : "Found in the code"} />
            <Kpi icon={LuPlug} tone="pink" k="External interfaces" v={o.external_interfaces} hint={undocumented ? `${undocumented} found only in code` : "All documented"} />
          </div>

          <ApplicationMapCard caps={data.capabilities} onOpen={() => navigate(`${base}/graph`)} />

          {data.decommission && data.decommission.programs > 0 && (
            <div className="savings" style={{ marginBottom: 0 }}>
              <div className="big">−{fmtNumber(data.decommission.lines)}<small>lines</small></div>
              <div>
                <b>Retire dead code before you migrate</b>
                <p>
                  {fmtNumber(data.decommission.programs)} program(s) are never called, run by a job or exposed as an API. Removing them cuts
                  about {Math.round(data.decommission.share * 100)}% of the code you would otherwise convert, test and run.
                  {data.decommission.hubs > 0 && ` ${data.decommission.hubs} critical hub program(s) need the most careful, phased cut-over.`}
                </p>
              </div>
            </div>
          )}

          <div className="grid-2">
            <RepositoryCard files={files} overview={o} onOpen={openSource} />
            <CodeCard data={data} onOpen={openSource} />
          </div>

          <InventoryCard o={o} data={data} />

          <ProgramsCard programs={data.programs} onOpen={openSource} />

          <div data-tour="launcher">
            <h2 style={{ marginBottom: 12 }}>Recommended next steps</h2>
            <div className="launch">
              {nextSteps(data.overview as unknown as Record<string, number>).map((s) => (
                <button key={s.title} className="launch-btn" onClick={() => navigate(`${base}/${s.path}`)}>
                  <span className="ico" style={s.urgent ? { background: "var(--amber-soft)", color: "var(--amber-ink)" } : undefined}><s.icon size={18} /></span>
                  <b>{s.title}</b>
                  <span>{s.text}</span>
                </button>
              ))}
            </div>
          </div>

          {(data.sensitive_data || data.data_hotspots) && (
            <div className="grid-2">
              {data.sensitive_data && <SensitiveDataCard data={data.sensitive_data} onOpen={openSource} />}
              {data.data_hotspots && <DataHotspotsCard rows={data.data_hotspots} onOpen={openSource} />}
            </div>
          )}

          <div className="grid-2">
              <div className="card">
                <div className="card-head"><h3><LuArchive size={17} className="h-ico" /> Unused and low-use components</h3><span className="subtle">Retirement candidates</span></div>
                <div className="table-wrap" style={{ maxHeight: 220 }}>
                  <table className="data">
                    <tbody>
                      {data.unused_components.length === 0 && <tr><td className="subtle">Nothing unused was found.</td></tr>}
                      {data.unused_components.map((u) => (
                        <tr key={u.type + u.name} className={u.file ? "clickable" : ""} onClick={() => u.file && openSource(u.file, u.line ?? 1)}>
                          <td><span className="tag">{u.type}</span></td>
                          <td className="mono" style={{ fontSize: 13 }}>{u.name}</td>
                          <td className="subtle" style={{ fontSize: 13 }}>{u.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <div className="card">
                <div className="card-head"><h3><LuPlug size={17} className="h-ico" /> External interfaces</h3></div>
                <div className="table-wrap" style={{ maxHeight: 220 }}>
                  <table className="data">
                    <tbody>
                      {data.external_interfaces.map((x) => (
                        <tr key={x.id}>
                          <td className="mono" style={{ fontSize: 13 }}>{x.name}</td>
                          <td>{x.documented ? <span className="tag tag-green">Documented</span> : <span className="tag tag-amber">Discovered in code</span>}</td>
                          <td className="subtle" style={{ fontSize: 13 }}>{x.description} · used by {x.used_by.join(", ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
          </div>
          {o.warnings.length > 0 && (
            <div className="warn-box">{o.warnings.length} parser warning(s): {o.warnings.slice(0, 3).join("; ")}</div>
          )}
        </div>
      )}
    </AppShell>
  );
}

const pct = (part: number, whole: number) => (whole ? Math.round((part / whole) * 100) : 0);

function Kpi({ icon: Icon, tone, k, v, hint }: { icon: typeof LuFiles; tone: string; k: string; v: number; hint: string }) {
  return (
    <div className={`kpi kpi-${tone}`}>
      <div className="kpi-top"><span className="kpi-ico"><Icon size={18} /></span><span className="kpi-k">{k}</span></div>
      <div className="kpi-v">{fmtNumber(v)}</div>
      <div className="kpi-hint">{hint}</div>
    </div>
  );
}
