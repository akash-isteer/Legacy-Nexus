import { useEffect, useState } from "react";
import { LuRefreshCw, LuScanSearch } from "react-icons/lu";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Link } from "../lib/router";
import { useToast } from "./ui";

export type IndexState = {
  enabled: boolean; status: string; model_key: string | null; chunk_count: number; embedded_count: number; dim: number;
  store: string | null; error: string | null; building: boolean; stale_reason: string | null; usable: boolean; search_mode: "hybrid" | "keyword";
};

/** Shows whether questions use hybrid or keyword search for this project, with index build progress. */
export function SemanticIndexCard({ projectId }: { projectId: number }) {
  const toast = useToast();
  const { user } = useAuth();
  const [state, setState] = useState<IndexState | null>(null);
  const [starting, setStarting] = useState(false);

  const load = () => api.get<IndexState>(`/projects/${projectId}/semantic-index`).then(setState).catch(() => {});
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);
  useEffect(() => {
    if (!state?.building && state?.status !== "building") return;
    const t = setTimeout(load, 1500);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  const build = async () => {
    setStarting(true);
    try {
      setState(await api.post<IndexState>(`/projects/${projectId}/semantic-index`));
      toast("Building the semantic index");
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setStarting(false);
    }
  };

  if (!state) return null;
  const building = state.building || state.status === "building";
  const pct = state.chunk_count ? Math.round((state.embedded_count / state.chunk_count) * 100) : 0;

  return (
    <div className="card card-pad" data-tour="ask-search-mode">
      <h3 className="row" style={{ marginBottom: 8 }}><LuScanSearch size={18} /> Search mode</h3>
      {state.usable ? (
        <>
          <span className="tag tag-green">Hybrid: keyword + semantic</span>
          <p className="subtle" style={{ marginTop: 8 }}>
            {state.embedded_count} chunks · {state.dim}-d · <span className="mono">{state.model_key}</span> · {state.store === "pgvector" ? "pgvector" : "file index"}
          </p>
        </>
      ) : (
        <span className="tag">Keyword search</span>
      )}
      {!state.enabled && (
        <p className="muted" style={{ fontSize: 14, marginTop: 8 }}>
          Turn on semantic search in <Link to="/settings">Settings</Link> to also match questions by meaning.
        </p>
      )}
      {building && (
        <div style={{ marginTop: 10 }}>
          <div className="row" style={{ fontSize: 13 }}><span className="spinner" style={{ width: 14, height: 14 }} /> Indexing {state.embedded_count}/{state.chunk_count} chunks</div>
          <div className="progress" style={{ marginTop: 6 }}><span style={{ width: `${pct}%` }} /></div>
        </div>
      )}
      {state.enabled && !building && state.stale_reason && <div className="warn-box" style={{ marginTop: 10, fontSize: 13 }}>{state.stale_reason} Rebuild to use semantic search.</div>}
      {state.enabled && !building && state.status === "failed" && <div className="error-box" style={{ marginTop: 10, fontSize: 13 }}>Indexing failed: {state.error}</div>}
      {state.enabled && !building && !state.usable && state.status === "none" && <p className="muted" style={{ fontSize: 14, marginTop: 8 }}>Build the index once to enable hybrid search for this project.</p>}
      {state.enabled && !building && user && (
        <button className="btn btn-sm" style={{ marginTop: 10 }} onClick={build} disabled={starting}>
          {starting ? <span className="spinner" /> : <LuRefreshCw size={14} />} {state.status === "none" ? "Build semantic index" : "Rebuild index"}
        </button>
      )}
    </div>
  );
}
