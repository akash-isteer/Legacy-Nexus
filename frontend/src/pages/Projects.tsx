import { useEffect, useRef, useState } from "react";
import { LuDatabase, LuFolderOpen, LuPlay, LuPlus, LuRefreshCw, LuSparkles, LuTrash2, LuUpload } from "react-icons/lu";
import { AppShell } from "../components/AppShell";
import { usePageTour } from "../components/Tour";
import { DASHBOARD_TOUR } from "../components/tours";
import { EmptyState, Loading, Modal, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { fmtDate, fmtNumber } from "../lib/format";
import { useApi } from "../lib/hooks";
import { navigate } from "../lib/router";
import type { Project } from "../lib/types";

export function ProjectsPage() {
  const { user, updatePreferences } = useAuth();
  const toast = useToast();
  const { data: projects, loading, reload } = useApi<Project[]>("/projects");
  const [creating, setCreating] = useState(false);
  const [busySample, setBusySample] = useState(false);
  const welcomed = Boolean(user?.preferences?.welcome_seen);
  const canWrite = user?.role === "admin" || user?.role === "analyst";
  usePageTour("dashboard", DASHBOARD_TOUR, welcomed && !loading);

  // poll while any project is analysing
  useEffect(() => {
    if (!projects?.some((p) => p.status === "analyzing")) return;
    const t = setTimeout(reload, 1500);
    return () => clearTimeout(t);
  }, [projects, reload]);

  const loadSample = async () => {
    setBusySample(true);
    try {
      const p = await api.post<Project>("/projects/sample");
      toast("Sample application loaded. Analysis is running.");
      navigate(`/projects/${p.id}`);
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusySample(false);
    }
  };

  const remove = async (p: Project) => {
    if (!confirm(`Delete “${p.name}” and all of its analysis results? This cannot be undone.`)) return;
    try {
      await api.del(`/projects/${p.id}`);
      toast("Project deleted");
      reload();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  return (
    <AppShell title="Projects">
      <div className="page-head">
        <div>
          <h1>Applications</h1>
          <p>Each project is one legacy application: its sources, its knowledge graph and everything derived from it.</p>
        </div>
        {canWrite && (
          <div className="row">
            <button className="btn" onClick={loadSample} disabled={busySample} data-tour="load-sample">
              {busySample ? <span className="spinner" /> : <LuSparkles size={16} />} Load sample application
            </button>
            <button className="btn btn-primary" onClick={() => setCreating(true)} data-tour="new-project">
              <LuPlus size={16} /> New project
            </button>
          </div>
        )}
      </div>

      <div className="card" data-tour="project-list">
        {loading && !projects ? (
          <Loading />
        ) : !projects?.length ? (
          <EmptyState icon={<LuDatabase size={36} />} title="No applications yet">
            {canWrite
              ? "Use the buttons above: load the sample banking system to explore, or create a project and upload your own code (COBOL, Java, .NET, Python, JavaScript and more)."
              : "No applications have been shared with you yet. Ask an analyst or administrator to add one."}
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>Application</th><th>Status</th><th className="num">Programs</th><th className="num">Business rules</th><th className="num">Lines of code</th><th>Analysed</th><th /></tr>
              </thead>
              <tbody>
                {projects.map((p) => (
                  <tr key={p.id} className="clickable" onClick={() => navigate(`/projects/${p.id}`)}>
                    <td>
                      <div style={{ fontWeight: 500 }}>{p.name}</div>
                      {p.description && <div className="subtle" style={{ maxWidth: 520 }}>{p.description}</div>}
                    </td>
                    <td style={{ minWidth: 200 }}><StatusCell p={p} /></td>
                    <td className="num">{fmtNumber(p.stats.programs_analysed)}</td>
                    <td className="num">{fmtNumber(p.stats.business_rules)}</td>
                    <td className="num">{fmtNumber(p.stats.lines_of_code)}</td>
                    <td className="subtle">{fmtDate(p.analyzed_at)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      {canWrite && (
                        <button className="btn btn-ghost btn-icon btn-sm btn-danger" title="Delete project" aria-label={`Delete ${p.name}`} onClick={() => remove(p)}>
                          <LuTrash2 size={15} />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {creating && <NewProjectModal onClose={() => setCreating(false)} onCreated={(id) => navigate(`/projects/${id}`)} />}
      {user && !welcomed && canWrite && <WelcomeModal onClose={() => updatePreferences({ welcome_seen: true }).catch(() => {})} onSample={async () => {
        await updatePreferences({ welcome_seen: true }).catch(() => {});
        loadSample();
      }} />}
    </AppShell>
  );
}

export function StatusCell({ p }: { p: Project }) {
  if (p.status === "analyzing")
    return (
      <div className="stack-sm" style={{ gap: 4 }}>
        <span className="tag tag-amber"><span className="spinner" style={{ width: 10, height: 10 }} /> Analysing · {p.progress}%</span>
        <div className="progress"><span style={{ width: `${p.progress}%` }} /></div>
        <span className="subtle">{p.stage}</span>
      </div>
    );
  if (p.status === "ready") return <span className="tag tag-green">Ready</span>;
  if (p.status === "failed") return <span className="tag tag-red" title={p.error ?? ""}>Failed</span>;
  if (p.status === "uploaded") return <span className="tag tag-blue">Uploaded · not analysed</span>;
  return <span className="tag">Empty</span>;
}

function WelcomeModal({ onClose, onSample }: { onClose: () => void; onSample: () => void }) {
  return (
    <Modal title="Welcome to LegacyNexus" onClose={onClose} width={600}
      footer={<>
        <button className="btn btn-ghost" onClick={onClose}>Show me around</button>
        <button className="btn btn-signal" onClick={onSample}><LuPlay size={15} /> Start with the sample</button>
      </>}>
      <p className="muted">Here is how to get from a pile of legacy code to answers in four steps.</p>
      <div className="steps">
        {[
          ["Create a project", "One project per application. Load the sample, or upload COBOL, JCL, SQL, Java, C#, Python, JavaScript/TypeScript, shell scripts, YAML config, OpenAPI specs and notes (files or .zip)."],
          ["Run the analysis", "Real parsers build the Legacy Knowledge Graph: programs, calls, SQL, files, jobs, rules and data lineage. No AI tokens are used."],
          ["Explore the results", "Use the buttons on the project page: dependency graph, impact analysis, business rules, data lineage, documentation and modernization plan."],
          ["Ask questions", "Pick an AI model and enter its key in Settings, then ask in plain language. Answers come with verified line citations and a confidence score."],
        ].map(([t, d], i) => (
          <div className="step" key={t}>
            <span className="n">{i + 1}</span>
            <div><b>{t}</b><span>{d}</span></div>
          </div>
        ))}
      </div>
      <p className="subtle" style={{ marginTop: 16 }}>Each page has its own short guide. Open it again with the Guide button in the top bar.</p>
    </Modal>
  );
}

function NewProjectModal({ onClose, onCreated }: { onClose: () => void; onCreated: (id: number) => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    folderInput.current?.setAttribute("webkitdirectory", "");
  }, []);

  const add = (list: FileList | null) => {
    if (!list) return;
    setFiles((prev) => [...prev, ...Array.from(list)]);
  };

  const submit = async () => {
    if (!name.trim()) return setError("Give the application a name.");
    if (!files.length) return setError("Add at least one source file or a .zip archive.");
    setBusy(true);
    setError(null);
    try {
      const p = await api.post<Project>("/projects", { name, description });
      const form = new FormData();
      files.forEach((f) => form.append("files", f, (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name));
      await api.upload(`/projects/${p.id}/files`, form);
      await api.post(`/projects/${p.id}/analyze`);
      toast("Upload complete. Analysis is running.");
      onCreated(p.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const size = files.reduce((a, f) => a + f.size, 0);

  return (
    <Modal title="New project" onClose={onClose} width={620}
      footer={<>
        <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" onClick={submit} disabled={busy}>
          {busy ? <span className="spinner" /> : <LuPlay size={15} />} Upload and analyse
        </button>
      </>}>
      <div className="stack">
        <div className="field">
          <label htmlFor="pname">Application name</label>
          <input id="pname" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Retail Banking Core" autoFocus />
        </div>
        <div className="field">
          <label htmlFor="pdesc">Description (optional)</label>
          <input id="pdesc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What this application does, who owns it" />
        </div>
        <div
          className={`dropzone ${over ? "over" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); add(e.dataTransfer.files); }}
          onClick={() => fileInput.current?.click()}
          role="button" tabIndex={0}
          onKeyDown={(e) => e.key === "Enter" && fileInput.current?.click()}
        >
          <LuUpload size={28} color="var(--text-3)" />
          <div style={{ fontWeight: 500, marginTop: 8 }}>Drop source files or a .zip here, or click to browse</div>
          <div className="hint" style={{ marginTop: 4 }}>.cbl .cob .cpy .jcl .sql .ddl .yaml .json .md .txt · extension-less mainframe members are detected by content</div>
          <input ref={fileInput} type="file" multiple hidden onChange={(e) => add(e.target.files)} />
        </div>
        <div className="row">
          <button className="btn btn-sm" type="button" onClick={() => folderInput.current?.click()}><LuFolderOpen size={14} /> Add a whole folder</button>
          <input ref={folderInput} type="file" multiple hidden onChange={(e) => add(e.target.files)} />
          {files.length > 0 && (
            <>
              <span className="subtle">{files.length} file(s) · {(size / 1024 / 1024).toFixed(2)} MB</span>
              <div className="spacer" />
              <button className="btn btn-ghost btn-sm" onClick={() => setFiles([])}><LuRefreshCw size={14} /> Clear</button>
            </>
          )}
        </div>
        {error && <div className="error-box">{error}</div>}
      </div>
    </Modal>
  );
}
