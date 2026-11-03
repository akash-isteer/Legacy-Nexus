import { useEffect, useState } from "react";
import { LuCircleAlert, LuCircleCheck, LuEye, LuEyeOff, LuKeyRound, LuSave, LuScanSearch, LuZap } from "react-icons/lu";
import { api } from "../lib/api";
import { fmtDate } from "../lib/format";
import { useApi } from "../lib/hooks";
import { ErrorBox, Loading, useToast } from "./ui";

type Provider = { label: string; fields: string[]; default_base_url: string; default_api_version?: string; model_suggestions: string[]; key_hint: string; base_url_hint?: string };
export type EmbeddingSettings = {
  enabled: boolean; provider: string; model: string; base_url: string; api_version: string; deployment: string;
  api_key_set: boolean; api_key_masked: string; reuse_llm_key: boolean; batch_size: number; max_chars: number;
  semantic_weight: number; min_semantic_score: number; auto_index: boolean; configured: boolean; active: boolean;
  model_key: string | null; vector_store: string; updated_at: string;
};

/** Settings card for semantic (embedding) search: provider, model, key, tuning and a connection test. */
export function SemanticSettings({ isAdmin }: { isAdmin: boolean }) {
  const toast = useToast();
  const { data: providers } = useApi<Record<string, Provider>>("/settings/embedding-providers");
  const { data: current, error, setData } = useApi<EmbeddingSettings>("/settings/embeddings");
  const [form, setForm] = useState<Partial<EmbeddingSettings> & { api_key?: string }>({});
  const [showKey, setShowKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; message: string; latency_ms?: number } | null>(null);

  useEffect(() => {
    if (current) setForm({ ...current, api_key: "" });
  }, [current]);

  if (error) return <ErrorBox message={error} />;
  if (!providers || !current) return <div className="card"><Loading /></div>;

  const provider = form.provider && form.provider !== "none" ? providers[form.provider] : null;
  const has = (f: string) => provider?.fields.includes(f);
  const set = (k: string, v: unknown) => setForm((f) => ({ ...f, [k]: v }));
  const local = form.provider === "ollama";

  const pickProvider = (key: string) => {
    const p = providers[key];
    setTest(null);
    setForm((f) => ({ ...f, provider: key, model: p?.model_suggestions[0] ?? "", base_url: key === current.provider ? current.base_url : "",
      api_version: p?.default_api_version ?? "", deployment: "" }));
  };

  const save = async () => {
    setSaving(true);
    try {
      const body: Record<string, unknown> = {
        enabled: form.enabled, provider: form.provider, model: form.model ?? "", base_url: form.base_url ?? "",
        api_version: form.api_version ?? "", deployment: form.deployment ?? "", reuse_llm_key: form.reuse_llm_key,
        batch_size: Number(form.batch_size), max_chars: Number(form.max_chars), semantic_weight: Number(form.semantic_weight),
        min_semantic_score: Number(form.min_semantic_score), auto_index: form.auto_index,
      };
      if (form.api_key) body.api_key = form.api_key;
      const saved = await api.put<EmbeddingSettings>("/settings/embeddings", body);
      setData(saved);
      toast(saved.active ? "Semantic search settings saved. Build the index from Ask the code." : "Semantic search settings saved");
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setSaving(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      setTest(await api.post<{ ok: boolean; message: string; latency_ms?: number }>("/settings/embeddings/test"));
    } catch (e) {
      setTest({ ok: false, message: (e as Error).message });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="card" data-tour="semantic-settings">
      <div className="card-head">
        <h3><LuScanSearch size={18} /> Semantic search (hybrid)</h3>
        {current.active ? <span className="tag tag-green">On · {current.model_key}</span> : <span className="tag">Keyword search only</span>}
      </div>
      <div className="card-body stack">
        <p className="muted" style={{ fontSize: 14 }}>
          Adds meaning-based search next to keyword search, so questions in business language find code that uses different words
          (for example “charged for going negative” finds <span className="mono">WS-OVERDRAFT-FEE</span>). Both rankings are merged.
          Parsing and analysis are unchanged and never use embeddings.
        </p>
        <label className="check">
          <input type="checkbox" checked={!!form.enabled} disabled={!isAdmin} onChange={(e) => set("enabled", e.target.checked)} />
          Enable semantic search
        </label>
        <div>
          <div className="label" style={{ marginBottom: 8 }}>Embedding provider</div>
          <div className="row-wrap">
            {Object.entries(providers).map(([k, p]) => (
              <button key={k} className={`chip ${form.provider === k ? "on" : ""}`} disabled={!isAdmin} onClick={() => pickProvider(k)}>{p.label.split(" (")[0]}</button>
            ))}
          </div>
          {provider && <div className="hint" style={{ marginTop: 8 }}>{provider.label}</div>}
        </div>
        {provider && !local && (
          <div className="warn-box">
            Building the index sends every code chunk (not just the excerpts used for answers) to {provider.label.split(" (")[0]}.
            For confidential code, use Ollama or a private endpoint.
          </div>
        )}
        {provider && (
          <div className="grid-2">
            {has("model") && (
              <div className="field">
                <label htmlFor="emodel">Embedding model</label>
                <input id="emodel" className="input input-mono" list="emodel-suggestions" value={form.model ?? ""} disabled={!isAdmin} onChange={(e) => set("model", e.target.value)} />
                <datalist id="emodel-suggestions">{provider.model_suggestions.map((m) => <option key={m} value={m} />)}</datalist>
                <span className="hint">{local ? "Pull it first, e.g. ollama pull nomic-embed-text" : "Pick a suggestion or type any embedding model id."}</span>
              </div>
            )}
            {has("deployment") && (
              <div className="field">
                <label htmlFor="edep">Deployment name</label>
                <input id="edep" className="input input-mono" value={form.deployment ?? ""} disabled={!isAdmin} onChange={(e) => set("deployment", e.target.value)} />
              </div>
            )}
            {has("api_key") && (
              <div className="field">
                <label htmlFor="ekey"><LuKeyRound size={13} style={{ verticalAlign: -1 }} /> API key</label>
                <div className="row" style={{ gap: 6 }}>
                  <input id="ekey" className="input input-mono" type={showKey ? "text" : "password"} autoComplete="off" disabled={!isAdmin || !!form.reuse_llm_key}
                    value={form.api_key ?? ""} onChange={(e) => set("api_key", e.target.value)}
                    placeholder={form.reuse_llm_key ? "Using the AI model key" : current.api_key_set ? `Saved: ${current.api_key_masked} (leave blank to keep)` : provider.key_hint} />
                  <button className="btn btn-icon" type="button" onClick={() => setShowKey((s) => !s)} aria-label={showKey ? "Hide key" : "Show key"}>{showKey ? <LuEyeOff size={16} /> : <LuEye size={16} />}</button>
                </div>
                <label className="check" style={{ marginTop: 4 }}>
                  <input type="checkbox" checked={!!form.reuse_llm_key} disabled={!isAdmin} onChange={(e) => set("reuse_llm_key", e.target.checked)} />
                  Use the same key as the AI model (same provider)
                </label>
              </div>
            )}
            {has("base_url") && (
              <div className="field">
                <label htmlFor="ebase">{form.provider === "azure_openai" ? "Endpoint" : "Base URL"}</label>
                <input id="ebase" className="input input-mono" value={form.base_url ?? ""} disabled={!isAdmin} onChange={(e) => set("base_url", e.target.value)} placeholder={provider.base_url_hint || provider.default_base_url || ""} />
                {provider.default_base_url && <span className="hint">Leave blank for {provider.default_base_url}</span>}
              </div>
            )}
            {has("api_version") && (
              <div className="field">
                <label htmlFor="ever">API version</label>
                <input id="ever" className="input input-mono" value={form.api_version ?? ""} disabled={!isAdmin} onChange={(e) => set("api_version", e.target.value)} />
              </div>
            )}
          </div>
        )}
        <details>
          <summary style={{ cursor: "pointer", fontWeight: 500 }}>Ranking and indexing controls</summary>
          <div className="grid-3" style={{ marginTop: 14 }}>
            <Num id="sw" label="Semantic weight" hint="Weight of semantic results in the merged ranking (keyword = 1)" value={form.semantic_weight} step={0.1} disabled={!isAdmin} onChange={(v) => set("semantic_weight", v)} />
            <Num id="ms" label="Minimum similarity" hint="Semantic matches below this cosine similarity are ignored. Calibrate per model." value={form.min_semantic_score} step={0.05} disabled={!isAdmin} onChange={(v) => set("min_semantic_score", v)} />
            <Num id="mc" label="Characters per chunk" hint="Text sent per code chunk when indexing" value={form.max_chars} step={500} disabled={!isAdmin} onChange={(v) => set("max_chars", v)} />
            <Num id="bs" label="Batch size" hint="Chunks per embedding request" value={form.batch_size} step={8} disabled={!isAdmin} onChange={(v) => set("batch_size", v)} />
            <label className="check" style={{ alignSelf: "center" }}>
              <input type="checkbox" checked={!!form.auto_index} disabled={!isAdmin} onChange={(e) => set("auto_index", e.target.checked)} />
              Build the index automatically after each analysis
            </label>
          </div>
        </details>
        {test && (
          <div className={test.ok ? "info-box" : "error-box"}>
            {test.ok ? <LuCircleCheck size={15} style={{ verticalAlign: -2 }} /> : <LuCircleAlert size={15} style={{ verticalAlign: -2 }} />} {test.message}
            {test.latency_ms !== undefined && ` (${(test.latency_ms / 1000).toFixed(1)}s)`}
          </div>
        )}
        {isAdmin && (
          <div className="row">
            <span className="subtle">Vector store: <b>{current.vector_store === "pgvector" ? "PostgreSQL pgvector" : "local file index"}</b> · updated {fmtDate(current.updated_at)}</span>
            <div className="spacer" />
            <button className="btn" onClick={runTest} disabled={testing || !current.configured} title={!current.configured ? "Save a complete configuration first" : ""}>
              {testing ? <span className="spinner" /> : <LuZap size={15} />} Test connection
            </button>
            <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? <span className="spinner" /> : <LuSave size={15} />} Save settings</button>
          </div>
        )}
      </div>
    </div>
  );
}

function Num({ id, label, hint, value, onChange, step = 1, disabled }: { id: string; label: string; hint: string; value: number | undefined; onChange: (v: number) => void; step?: number; disabled?: boolean }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} className="input" type="number" step={step} value={value ?? ""} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))} />
      <span className="hint">{hint}</span>
    </div>
  );
}
