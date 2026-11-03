import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { LuExternalLink, LuX } from "react-icons/lu";
import { api, qs } from "../lib/api";
import { Link } from "../lib/router";
import type { Project } from "../lib/types";
import { CodeView } from "./ui";

type ProjectState = {
  project: Project | null;
  error: string | null;
  reload: () => Promise<void>;
  openSource: (file: string, start?: number, end?: number) => void;
};

const ProjectContext = createContext<ProjectState | null>(null);

export function useProject() {
  const ctx = useContext(ProjectContext);
  if (!ctx) throw new Error("useProject must be used inside a project page");
  return ctx;
}

export function useOptionalProject() {
  return useContext(ProjectContext);
}

/** Loads a project, polls while it is being analysed, and hosts the source-code drawer. */
export function ProjectProvider({ projectId, children }: { projectId: number; children: ReactNode }) {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState<{ file: string; start?: number; end?: number } | null>(null);
  const timer = useRef<number | undefined>(undefined);

  const reload = useCallback(async () => {
    try {
      const p = await api.get<Project>(`/projects/${projectId}`);
      setProject(p);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId]);

  useEffect(() => {
    setProject(null);
    reload();
  }, [reload]);

  useEffect(() => {
    window.clearTimeout(timer.current);
    if (project && (project.status === "analyzing")) {
      timer.current = window.setTimeout(reload, 1500);
    }
    return () => window.clearTimeout(timer.current);
  }, [project, reload]);

  const openSource = useCallback((file: string, start?: number, end?: number) => setSource({ file, start, end }), []);

  return (
    <ProjectContext.Provider value={{ project, error, reload, openSource }}>
      {children}
      {source && <SourceDrawer projectId={projectId} {...source} onClose={() => setSource(null)} />}
    </ProjectContext.Provider>
  );
}

function SourceDrawer({ projectId, file, start, end, onClose }: { projectId: number; file: string; start?: number; end?: number; onClose: () => void }) {
  const [lines, setLines] = useState<string[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setLines(null);
    api
      .get<{ lines: string[] }>(`/projects/${projectId}/files/content${qs({ path: file })}`)
      .then((r) => setLines(r.lines))
      .catch((e) => setErr(e.message));
  }, [projectId, file]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" aria-label={`Source ${file}`}>
        <div className="drawer-head">
          <div style={{ minWidth: 0 }}>
            <div className="mono" style={{ fontWeight: 500 }}>{file}</div>
            {start && <div className="subtle">Lines {start}{end && end !== start ? `–${end}` : ""}</div>}
          </div>
          <div className="spacer" />
          <Link className="btn btn-sm" onClick={onClose} to={`/projects/${projectId}/files?path=${encodeURIComponent(file)}${start ? `&line=${start}` : ""}`}>
            <LuExternalLink size={14} /> Open in files
          </Link>
          <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close source"><LuX size={18} /></button>
        </div>
        {err && <div style={{ padding: 18 }}><div className="error-box">{err}</div></div>}
        {lines && <CodeView lines={lines} highlightFrom={start} highlightTo={end ?? start} />}
        {!lines && !err && <div style={{ padding: 18 }}><span className="spinner" /></div>}
      </aside>
    </>
  );
}
