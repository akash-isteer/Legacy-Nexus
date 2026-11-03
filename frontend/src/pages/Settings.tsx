import { useEffect, useState, type ReactNode } from "react";
import {
  LuBot, LuCircleAlert, LuCircleCheck, LuClipboardList, LuEye, LuEyeOff, LuGauge, LuKeyRound, LuLink, LuPencil, LuPlus,
  LuPower, LuRefreshCw, LuSave, LuScanSearch, LuServer, LuShield, LuSlidersHorizontal, LuTrash2, LuUserRound, LuUsers, LuZap,
} from "react-icons/lu";
import { AppShell } from "../components/AppShell";
import { SemanticSettings } from "../components/SemanticSettings";
import { EmptyState, ErrorBox, Loading, Modal, useToast } from "../components/ui";
import { api, qs } from "../lib/api";
import { useAuth } from "../lib/auth";
import { fmtDate, initials } from "../lib/format";
import { useApi } from "../lib/hooks";
import { navigate, useSearchParam } from "../lib/router";

type Provider = { label: string; fields: string[]; default_base_url: string; default_api_version?: string; model_suggestions: string[]; key_hint: string; base_url_hint?: string };
type SavedModel = {
  id: number; name: string; provider: string; provider_label: string; model: string; base_url: string; api_version: string;
  deployment: string; api_key_set: boolean; api_key_masked: string; is_active: boolean; configured: boolean;
  is_small: boolean; is_fallback: boolean; last_test_ok: boolean | null; last_tested_at: string | null; updated_at: string;
};
type Usage = {
  days: number; cache_hit_rate: number; month_tokens: number; monthly_token_budget: number | null; project_token_budget: number | null;
  total: { requests: number; cached: number; failed: number; input_tokens: number; output_tokens: number };
  by_purpose: Record<string, { requests: number; cached: number; tokens: number }>;
  recent: { id: number; created_at: string; purpose: string; provider: string; model: string; tier: string; cached: boolean; ok: boolean;
    error: string | null; input_tokens: number; output_tokens: number; latency_ms: number; prompt_chars: number; redactions: number;
    sources: Record<string, unknown>[]; user: string | null }[];
};
type Ready = { profile: string; environment: string; components: Record<string, { ok: boolean; kind: string; where: string; error?: string }> };
type AISettings = { temperature: number; max_output_tokens: number; top_k: number; context_char_budget: number; min_retrieval_score: number; provider: string; configured: boolean };
type Audit = { total: number; items: { id: number; action: string; target: string; detail: Record<string, unknown>; ip_address: string; user: string | null; created_at: string }[] };
type AdminUserRow = { id: number; email: string; full_name: string; role: string; is_active: boolean; created_at: string; last_login_at: string | null; projects: number };

const TABS = [
  { id: "account", label: "My account", icon: LuUserRound, admin: false },
  { id: "models", label: "AI models", icon: LuBot, admin: false },
  { id: "search", label: "Semantic search", icon: LuScanSearch, admin: false },
  { id: "usage", label: "AI usage & budgets", icon: LuGauge, admin: true },
  { id: "users", label: "Users", icon: LuUsers, admin: true },
  { id: "deployment", label: "Deployment", icon: LuServer, admin: true },
  { id: "audit", label: "Audit log", icon: LuClipboardList, admin: true },
];

export function SettingsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const requested = useSearchParam("tab");
  const tabs = TABS.filter((t) => isAdmin || !t.admin);
  const tab = tabs.find((t) => t.id === requested)?.id ?? (isAdmin ? "models" : "account");
  return (
    <AppShell title="Settings">
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>Your account, the AI models used for questions, search, users and the audit trail. Analysis itself (graphs, rules, impact, lineage) never calls an AI model.</p>
        </div>
      </div>
      <div className="tabs" role="tablist">
        {tabs.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={`tab ${tab === t.id ? "on" : ""}`}
            onClick={() => navigate(`/settings?tab=${t.id}`, true)}>
            <t.icon size={16} /> {t.label}
          </button>
        ))}
      </div>
      <div style={{ maxWidth: 1000 }}>
        {tab === "account" && <AccountTab />}
        {tab === "models" && <ModelsTab isAdmin={isAdmin} />}
        {tab === "search" && <SemanticSettings isAdmin={isAdmin} />}
        {tab === "usage" && isAdmin && <UsageTab />}
        {tab === "users" && isAdmin && <UsersTab />}
        {tab === "deployment" && isAdmin && <DeploymentTab />}
        {tab === "audit" && isAdmin && <AuditLog />}
      </div>
    </AppShell>
  );
}

/* ------------------------------------------------------------------ account */

function AccountTab() {
  const { user, refresh } = useAuth();
  const toast = useToast();
  const [name, setName] = useState(user?.full_name ?? "");
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);

  const saveName = async () => {
    try {
      await api.patch("/auth/me", { full_name: name.trim() });
      await refresh();
      toast("Profile updated");
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const changePassword = async () => {
    if (next !== confirm) return toast("The two new passwords do not match", true);
    setBusy(true);
    try {
      await api.post("/auth/change-password", { current_password: current, new_password: next });
      setCurrent(""); setNext(""); setConfirm("");
      toast("Password changed");
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="card">
        <div className="card-head"><h3><LuUserRound size={18} /> Profile</h3><span className="tag">{user?.role}</span></div>
        <div className="card-body stack">
          <div className="row" style={{ gap: 14 }}>
            <span className="avatar" style={{ width: 48, height: 48, fontSize: 17 }}>{initials(user?.full_name || user?.email || "")}</span>
            <div><div style={{ fontWeight: 600 }}>{user?.full_name}</div><div className="subtle">{user?.email}</div></div>
          </div>
          <div className="grid-2">
            <div className="field"><label htmlFor="fullname">Full name</label><input id="fullname" className="input" value={name} onChange={(e) => setName(e.target.value)} /></div>
            <div className="field"><label>Email</label><input className="input" value={user?.email ?? ""} disabled /><span className="hint">Contact an administrator to change your email.</span></div>
          </div>
          <div className="row"><span className="spacer" /><button className="btn btn-primary" onClick={saveName} disabled={!name.trim() || name === user?.full_name}><LuSave size={15} /> Save profile</button></div>
        </div>
      </div>
      <div className="card">
        <div className="card-head"><h3><LuShield size={18} /> Change password</h3></div>
        <div className="card-body stack">
          <div className="grid-3">
            <div className="field"><label htmlFor="cur">Current password</label><input id="cur" className="input" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} /></div>
            <div className="field"><label htmlFor="new">New password</label><input id="new" className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} /></div>
            <div className="field"><label htmlFor="conf">Confirm new password</label><input id="conf" className="input" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} /></div>
          </div>
          <div className="row"><span className="hint">At least 8 characters, including a letter and a number.</span><span className="spacer" />
            <button className="btn btn-primary" onClick={changePassword} disabled={busy || !current || !next}>{busy ? <span className="spinner" /> : <LuKeyRound size={15} />} Change password</button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ AI models */

function ModelsTab({ isAdmin }: { isAdmin: boolean }) {
  const toast = useToast();
  const { data: providers } = useApi<Record<string, Provider>>("/settings/providers");
  const { data: models, error, reload } = useApi<SavedModel[]>("/settings/models");
  const [editing, setEditing] = useState<SavedModel | "new" | null>(null);
  const [testing, setTesting] = useState<number | null>(null);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      toast(ok);
      reload();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const test = async (m: SavedModel) => {
    setTesting(m.id);
    try {
      const r = await api.post<{ ok: boolean; message: string; latency_ms?: number }>(`/settings/models/${m.id}/test`);
      toast(`${m.name}: ${r.message}${r.latency_ms ? ` (${(r.latency_ms / 1000).toFixed(1)}s)` : ""}`, !r.ok);
      reload();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setTesting(null);
    }
  };
  const remove = (m: SavedModel) => {
    if (!confirm(`Delete the saved model “${m.name}”?${m.is_active ? " It is the active model, so questions will be answered with evidence only until you activate another." : ""}`)) return;
    act(() => api.del(`/settings/models/${m.id}`), "Model deleted");
  };

  if (error) return <ErrorBox message={error} />;
  if (!models || !providers) return <div className="card"><Loading /></div>;
  const active = models.find((m) => m.is_active);

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="card">
        <div className="card-head">
          <h3><LuBot size={18} /> Saved models</h3>
          <div className="row">
            {active ? <span className="tag tag-green">Active: {active.name}</span> : <span className="tag tag-amber">No active model · evidence-only answers</span>}
            {isAdmin && <button className="btn btn-primary btn-sm" onClick={() => setEditing("new")}><LuPlus size={14} /> Add model</button>}
          </div>
        </div>
        <div className="card-body">
          {!isAdmin && <div className="info-box" style={{ marginBottom: 14 }}>Only administrators can add, change or remove models.</div>}
          {models.length === 0 ? (
            <EmptyState icon={<LuBot size={34} />} title="No models saved yet"
              action={isAdmin && <button className="btn btn-primary" onClick={() => setEditing("new")}><LuPlus size={15} /> Add your first model</button>}>
              Save one or more AI model connections (Azure OpenAI, Gemini, Claude, OpenAI, or a local Ollama model) and choose which one answers questions.
            </EmptyState>
          ) : (
            <div className="model-list">
              {models.map((m) => (
                <div key={m.id} className={`model-row ${m.is_active ? "active" : ""}`}>
                  <span className="model-icon">{m.provider_label.slice(0, 2)}</span>
                  <div style={{ minWidth: 0 }}>
                    <div className="row" style={{ gap: 8 }}>
                      <span className="name">{m.name}</span>
                      {m.is_active && <span className="tag tag-green">Active</span>}
                      {m.is_small && <span className="tag tag-violet">Small model</span>}
                      {m.is_fallback && <span className="tag">Fallback</span>}
                      {!m.configured && <span className="tag tag-amber">Incomplete</span>}
                      {m.last_test_ok === true && <span className="tag tag-teal"><LuCircleCheck size={12} /> Tested</span>}
                      {m.last_test_ok === false && <span className="tag tag-red"><LuCircleAlert size={12} /> Test failed</span>}
                    </div>
                    <div className="meta">
                      {m.provider_label.split(" (")[0]} · <span className="mono">{m.model || m.deployment || "—"}</span>
                      {m.api_key_set && <> · key {m.api_key_masked}</>} · updated {fmtDate(m.updated_at)}
                    </div>
                  </div>
                  {isAdmin && (
                    <div className="row" style={{ gap: 6 }}>
                      {!m.is_active && <button className="btn btn-sm" onClick={() => act(() => api.post(`/settings/models/${m.id}/activate`), `${m.name} is now active`)} disabled={!m.configured}><LuPower size={14} /> Use this model</button>}
                      <button className="btn btn-sm btn-ghost" onClick={() => test(m)} disabled={testing === m.id || !m.configured} title="Test connection">{testing === m.id ? <span className="spinner" /> : <LuZap size={14} />}</button>
                      <button className="btn btn-sm btn-ghost" onClick={() => setEditing(m)} title="Edit"><LuPencil size={14} /></button>
                      <button className="btn btn-sm btn-ghost btn-danger" onClick={() => remove(m)} title="Delete"><LuTrash2 size={14} /></button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          {isAdmin && active && (
            <div className="row" style={{ marginTop: 14 }}>
              <span className="spacer" />
              <button className="btn btn-sm btn-ghost" onClick={() => act(() => api.post("/settings/models/deactivate"), "AI answers turned off")}>Turn off AI answers</button>
            </div>
          )}
        </div>
      </div>
      <QualityControls isAdmin={isAdmin} />
      {editing && <ModelEditor providers={providers} model={editing === "new" ? null : editing}
        onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} />}
    </div>
  );
}

function ModelEditor({ providers, model, onClose, onSaved }: { providers: Record<string, Provider>; model: SavedModel | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [form, setForm] = useState({
    name: model?.name ?? "", provider: model?.provider ?? Object.keys(providers)[0], model: model?.model ?? "",
    base_url: model?.base_url ?? "", api_version: model?.api_version ?? "", deployment: model?.deployment ?? "", api_key: "",
  });
  const [isSmall, setIsSmall] = useState(model?.is_small ?? false);
  const [isFallback, setIsFallback] = useState(model?.is_fallback ?? false);
  const [showKey, setShowKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const p = providers[form.provider];
  const has = (f: string) => p?.fields.includes(f);
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  useEffect(() => {
    if (!model && p) setForm((f) => ({ ...f, model: p.model_suggestions[0] ?? "", api_version: p.default_api_version ?? "" }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.provider]);

  const save = async () => {
    setBusy(true);
    try {
      const body: Record<string, unknown> = { ...form, is_small: isSmall, is_fallback: isFallback };
      if (!form.api_key) delete body.api_key;
      if (model) await api.put(`/settings/models/${model.id}`, body);
      else await api.post("/settings/models", body);
      toast(model ? "Model updated" : "Model saved");
      onSaved();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={model ? `Edit ${model.name}` : "Add an AI model"} onClose={onClose} width={640}
      footer={<><button className="btn btn-ghost" onClick={onClose}>Cancel</button><button className="btn btn-primary" onClick={save} disabled={busy}>{busy ? <span className="spinner" /> : <LuSave size={15} />} Save model</button></>}>
      <div className="stack">
        <div>
          <div className="label" style={{ marginBottom: 8 }}>Provider</div>
          <div className="row-wrap">
            {Object.entries(providers).map(([k, pv]) => (
              <button key={k} type="button" className={`chip ${form.provider === k ? "on" : ""}`} disabled={!!model} onClick={() => set("provider", k)}>{pv.label.split(" (")[0]}</button>
            ))}
          </div>
          {p && <div className="hint" style={{ marginTop: 6 }}>{p.label}</div>}
        </div>
        <div className="field">
          <label htmlFor="mname">Display name</label>
          <input id="mname" className="input" value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="e.g. Gemini for Q&A (leave blank for an automatic name)" />
        </div>
        <div className="grid-2">
          {has("model") && (
            <Field id="mmodel" label="Model">
              <input id="mmodel" className="input input-mono" list="msugg" value={form.model} onChange={(e) => set("model", e.target.value)} />
              <datalist id="msugg">{p.model_suggestions.map((s) => <option key={s} value={s} />)}</datalist>
            </Field>
          )}
          {has("deployment") && <Field id="mdep" label="Deployment name"><input id="mdep" className="input input-mono" value={form.deployment} onChange={(e) => set("deployment", e.target.value)} /></Field>}
          {has("api_key") && (
            <Field id="mkey" label="API key" hint={model?.api_key_set ? `Saved: ${model.api_key_masked}. Leave blank to keep it.` : "Encrypted at rest and never shown again."}>
              <div className="input-affix">
                <input id="mkey" className="input input-mono" type={showKey ? "text" : "password"} autoComplete="off" value={form.api_key} onChange={(e) => set("api_key", e.target.value)} placeholder={p.key_hint} />
                <button type="button" className="affix" onClick={() => setShowKey((s) => !s)} aria-label="Show key">{showKey ? <LuEyeOff size={16} /> : <LuEye size={16} />}</button>
              </div>
            </Field>
          )}
          {has("base_url") && <Field id="mbase" label={form.provider === "azure_openai" ? "Endpoint" : "Base URL"} hint={p.default_base_url ? `Leave blank for ${p.default_base_url}` : undefined}><input id="mbase" className="input input-mono" value={form.base_url} onChange={(e) => set("base_url", e.target.value)} placeholder={p.base_url_hint || p.default_base_url || ""} /></Field>}
          {has("api_version") && <Field id="mver" label="API version"><input id="mver" className="input input-mono" value={form.api_version} onChange={(e) => set("api_version", e.target.value)} /></Field>}
        </div>
        <div>
          <div className="label" style={{ marginBottom: 8 }}>Routing</div>
          <div className="stack-sm">
            <label className="check"><input type="checkbox" checked={isSmall} onChange={(e) => setIsSmall(e.target.checked)} /> Small model: use for summaries and other high-volume, low-cost work</label>
            <label className="check"><input type="checkbox" checked={isFallback} onChange={(e) => setIsFallback(e.target.checked)} /> Fallback: try this model automatically when the active model fails</label>
          </div>
        </div>
      </div>
    </Modal>
  );
}

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: ReactNode }) {
  return <div className="field"><label htmlFor={id}>{label}</label>{children}{hint && <span className="hint">{hint}</span>}</div>;
}

function QualityControls({ isAdmin }: { isAdmin: boolean }) {
  const toast = useToast();
  const { data, setData } = useApi<AISettings>("/settings/ai");
  const [form, setForm] = useState<Partial<AISettings>>({});
  useEffect(() => { if (data) setForm(data); }, [data]);
  if (!data) return null;
  const set = (k: keyof AISettings, v: number) => setForm((f) => ({ ...f, [k]: v }));
  const save = async () => {
    try {
      setData(await api.put<AISettings>("/settings/ai", {
        temperature: Number(form.temperature), max_output_tokens: Number(form.max_output_tokens), top_k: Number(form.top_k),
        context_char_budget: Number(form.context_char_budget), min_retrieval_score: Number(form.min_retrieval_score),
      }));
      toast("Answer quality settings saved");
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const num = (k: keyof AISettings, label: string, hint: string, step = 1) => (
    <div className="field">
      <label htmlFor={k}>{label}</label>
      <input id={k} className="input" type="number" step={step} value={(form[k] as number) ?? ""} disabled={!isAdmin} onChange={(e) => set(k, Number(e.target.value))} />
      <span className="hint">{hint}</span>
    </div>
  );
  return (
    <div className="card">
      <div className="card-head"><h3><LuSlidersHorizontal size={18} /> Answer quality and cost controls</h3><span className="subtle">Apply to whichever model is active</span></div>
      <div className="card-body stack">
        <div className="grid-3">
          {num("top_k", "Excerpts per question", "Code chunks sent to the model (1–20)")}
          {num("context_char_budget", "Context budget (characters)", "Hard cap on code sent per question", 1000)}
          {num("min_retrieval_score", "Minimum retrieval score", "Below this, answer “Not found” without calling the model", 0.5)}
          {num("temperature", "Temperature", "0 gives the most consistent answers", 0.1)}
          {num("max_output_tokens", "Max answer tokens", "128–8192", 100)}
        </div>
        {isAdmin && <div className="row"><span className="spacer" /><button className="btn btn-primary" onClick={save}><LuSave size={15} /> Save controls</button></div>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ users */

function UsersTab() {
  const toast = useToast();
  const { user: me } = useAuth();
  const { data, error, reload } = useApi<AdminUserRow[]>("/users");
  const [link, setLink] = useState<{ email: string; url: string } | null>(null);
  const update = async (u: AdminUserRow, body: Record<string, unknown>, ok: string) => {
    try {
      await api.patch(`/users/${u.id}`, body);
      toast(ok);
      reload();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const resetLink = async (u: AdminUserRow) => {
    try {
      const r = await api.post<{ emailed: boolean; reset_url: string | null }>(`/users/${u.id}/reset-link`);
      if (r.emailed) toast(`Reset link emailed to ${u.email}`);
      else if (r.reset_url) setLink({ email: u.email, url: r.reset_url });
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  if (error) return <ErrorBox message={error} />;
  if (!data) return <div className="card"><Loading /></div>;
  return (
    <div className="card">
      <div className="card-head"><h3><LuUsers size={18} /> Users</h3><span className="subtle">{data.length} accounts · {data.filter((u) => u.is_active).length} active</span></div>
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th>User</th><th>Role</th><th>Status</th><th className="num">Projects</th><th>Last sign-in</th><th /></tr></thead>
          <tbody>
            {data.map((u) => (
              <tr key={u.id}>
                <td><div className="row" style={{ gap: 10 }}><span className="avatar" style={{ width: 28, height: 28, fontSize: 12 }}>{initials(u.full_name || u.email)}</span><div><div style={{ fontWeight: 500 }}>{u.full_name}{u.id === me?.id && <span className="subtle"> (you)</span>}</div><div className="subtle">{u.email}</div></div></div></td>
                <td>
                  <select className="select" style={{ width: 130, height: 32 }} value={u.role} onChange={(e) => update(u, { role: e.target.value }, `${u.full_name} is now ${e.target.value}`)} aria-label={`Role of ${u.email}`}>
                    <option value="admin">Admin</option><option value="analyst">Analyst</option><option value="viewer">Viewer (read-only)</option>
                  </select>
                </td>
                <td>{u.is_active ? <span className="tag tag-green">Active</span> : <span className="tag">Disabled</span>}</td>
                <td className="num">{u.projects}</td>
                <td className="subtle">{fmtDate(u.last_login_at)}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  <button className="btn btn-sm btn-ghost" onClick={() => resetLink(u)} title="Create a password reset link"><LuLink size={14} /> Reset link</button>
                  {u.id !== me?.id && (u.is_active
                    ? <button className="btn btn-sm btn-ghost btn-danger" onClick={() => update(u, { is_active: false }, `${u.full_name} disabled`)}>Disable</button>
                    : <button className="btn btn-sm btn-ghost" onClick={() => update(u, { is_active: true }, `${u.full_name} enabled`)}>Enable</button>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card-body subtle" style={{ borderTop: "1px solid var(--line)" }}>
        <b>Admin</b> manages users, models and settings. <b>Analyst</b> creates projects, uploads code, runs analyses and reviews rules. <b>Viewer</b> can look at everything but change nothing.
      </div>
      {link && (
        <Modal title="Password reset link" onClose={() => setLink(null)} footer={<button className="btn btn-primary" onClick={() => { navigator.clipboard?.writeText(link.url); toast("Link copied"); }}>Copy link</button>}>
          <p className="muted">Email is not configured, so send this one-time link to <b>{link.email}</b> yourself. It expires soon and works once.</p>
          <div className="mono" style={{ marginTop: 12, padding: 10, background: "var(--surface-2)", borderRadius: 6, fontSize: 12.5, wordBreak: "break-all" }}>{link.url}</div>
        </Modal>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ AI usage & budgets */

function UsageTab() {
  const toast = useToast();
  const { data, reload } = useApi<Usage>("/settings/ai/usage?days=30&limit=30");
  const [monthly, setMonthly] = useState("");
  const [perProject, setPerProject] = useState("");
  useEffect(() => {
    if (data) {
      setMonthly(data.monthly_token_budget ? String(data.monthly_token_budget) : "");
      setPerProject(data.project_token_budget ? String(data.project_token_budget) : "");
    }
  }, [data]);
  if (!data) return <div className="card"><Loading /></div>;
  const t = data.total;
  const pct = data.monthly_token_budget ? Math.min(100, (data.month_tokens / data.monthly_token_budget) * 100) : 0;
  const saveBudgets = async () => {
    try {
      await api.put("/settings/ai", { monthly_token_budget: Number(monthly) || 0, project_token_budget: Number(perProject) || 0 });
      toast("Budgets saved");
      reload();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="stat-row" style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))" }}>
        <div className="stat"><div className="v">{t.requests}</div><div className="k">AI requests (30 days)</div></div>
        <div className="stat"><div className="v">{Math.round(data.cache_hit_rate * 100)}%</div><div className="k">Answered from cache (free)</div></div>
        <div className="stat"><div className="v">{(t.input_tokens + t.output_tokens).toLocaleString()}</div><div className="k">Tokens used (30 days)</div></div>
        <div className={`stat ${t.failed ? "alert" : ""}`}><div className="v">{t.failed}</div><div className="k">Failed requests</div></div>
      </div>

      <div className="card">
        <div className="card-head"><h3><LuGauge size={18} /> Token budgets</h3><span className="subtle">Hard stop: requests over budget are answered with evidence only</span></div>
        <div className="card-body stack">
          <div>
            <div className="row"><b>{data.month_tokens.toLocaleString()}</b><span className="subtle">tokens this month{data.monthly_token_budget ? ` of ${data.monthly_token_budget.toLocaleString()}` : " · no monthly cap"}</span></div>
            {data.monthly_token_budget && <div className="progress" style={{ marginTop: 8 }}><span style={{ width: `${pct}%`, background: pct > 90 ? "var(--red)" : undefined }} /></div>}
          </div>
          <div className="grid-2">
            <div className="field"><label htmlFor="bm">Monthly budget, whole organisation (tokens)</label><input id="bm" className="input" type="number" min={0} value={monthly} placeholder="No cap" onChange={(e) => setMonthly(e.target.value)} /></div>
            <div className="field"><label htmlFor="bp">Monthly budget per project (tokens)</label><input id="bp" className="input" type="number" min={0} value={perProject} placeholder="No cap" onChange={(e) => setPerProject(e.target.value)} /></div>
          </div>
          <div className="row"><span className="hint">Leave empty for no cap. Cached answers and graph answers never count.</span><span className="spacer" /><button className="btn btn-primary" onClick={saveBudgets}><LuSave size={15} /> Save budgets</button></div>
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h3>What was sent to which model</h3><button className="btn btn-sm btn-ghost" onClick={reload}><LuRefreshCw size={14} /> Refresh</button></div>
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>When</th><th>Feature</th><th>Model</th><th className="num">Tokens</th><th className="num">Prompt</th><th>Redacted</th><th>Result</th></tr></thead>
            <tbody>
              {data.recent.length === 0 && <tr><td colSpan={7} className="subtle">No AI requests yet. Graph answers and cached answers do not appear here as cost.</td></tr>}
              {data.recent.map((r) => (
                <tr key={r.id}>
                  <td className="subtle" style={{ whiteSpace: "nowrap" }}>{fmtDate(r.created_at)}<div>{r.user ?? ""}</div></td>
                  <td><span className="tag">{r.purpose.replace("_", " ")}</span> <span className="subtle">{r.tier}</span></td>
                  <td className="mono" style={{ fontSize: 12.5 }}>{r.provider} · {r.model}</td>
                  <td className="num">{r.cached ? <span className="tag tag-teal">cache</span> : (r.input_tokens + r.output_tokens).toLocaleString()}</td>
                  <td className="num subtle">{(r.prompt_chars / 1000).toFixed(1)}k chars · {r.sources.length} source(s)</td>
                  <td>{r.redactions ? <span className="tag tag-amber">{r.redactions}</span> : <span className="subtle">0</span>}</td>
                  <td>{r.ok ? <span className="tag tag-green">OK</span> : <span className="tag tag-red" title={r.error ?? ""}>Failed</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ deployment */

const COMPONENT_LABEL: Record<string, string> = {
  database: "Main database (projects, Code Q&A)", vectors: "Code Q&A vectors",
  docqa_database: "Document Q&A database", docqa_vectors: "Document Q&A vectors",
  storage: "File storage", jobs: "Background jobs",
};

function DeploymentTab() {
  const [data, setData] = useState<Ready | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => {
    fetch(`${(import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") || "/api"}/health/ready`)
      .then((r) => r.json()).then(setData).catch((e) => setError(String(e)));
  };
  useEffect(load, []);
  if (error) return <ErrorBox message={error} />;
  if (!data) return <div className="card"><Loading /></div>;
  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="card">
        <div className="card-head">
          <h3><LuServer size={18} /> This deployment</h3>
          <div className="row">
            <span className={`tag ${data.profile === "cloud" ? "tag-blue" : "tag-teal"}`}>{data.profile === "cloud" ? "Cloud profile" : "Local profile"}</span>
            <span className="tag">{data.environment}</span>
            <button className="btn btn-sm btn-ghost" onClick={load}><LuRefreshCw size={14} /> Check again</button>
          </div>
        </div>
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Service</th><th>Running on</th><th>Location</th><th>Status</th></tr></thead>
            <tbody>
              {Object.entries(data.components).map(([k, c]) => (
                <tr key={k}>
                  <td style={{ fontWeight: 600 }}>{COMPONENT_LABEL[k] ?? k}</td>
                  <td>{c.kind}</td>
                  <td className="mono subtle" style={{ fontSize: 12.5, wordBreak: "break-all" }}>{c.where}</td>
                  <td>{c.ok ? <span className="tag tag-green"><LuCircleCheck size={12} /> Healthy</span> : <span className="tag tag-red" title={c.error}><LuCircleAlert size={12} /> {c.error ?? "Unavailable"}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="card card-pad">
        <h3 style={{ marginBottom: 8 }}>Switching between local and cloud</h3>
        <p className="muted" style={{ fontSize: 14 }}>
          The same build runs in both. Set <span className="mono">DEPLOYMENT_PROFILE</span> to <b>local</b> or <b>cloud</b> and point the services at the right place:
          the database through <span className="mono">DATABASE_URL</span> (or <span className="mono">DB_HOST</span> / <span className="mono">DB_USER</span> / <span className="mono">DB_PASSWORD</span>),
          background jobs through <span className="mono">JOB_BACKEND</span> (inline or Azure Service Bus), files through <span className="mono">STORAGE_DIR</span> (local disk or a mounted Azure Files share).
          Database schema upgrades run automatically. For Azure, <span className="mono">infra/azure/deploy.ps1</span> creates everything and sets these values from Key Vault.
        </p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ audit */

function AuditLog() {
  const [offset, setOffset] = useState(0);
  const { data } = useApi<Audit>(`/settings/audit${qs({ limit: 20, offset })}`);
  return (
    <div className="card">
      <div className="card-head"><h3><LuClipboardList size={18} /> Audit log</h3><span className="subtle">{data?.total ?? 0} events</span></div>
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th>When</th><th>User</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead>
          <tbody>
            {data?.items.map((a) => (
              <tr key={a.id}>
                <td className="subtle" style={{ whiteSpace: "nowrap" }}>{fmtDate(a.created_at)}</td>
                <td style={{ fontSize: 13 }}>{a.user ?? "—"}</td>
                <td><span className="tag">{a.action.replace(/_/g, " ")}</span></td>
                <td className="mono" style={{ fontSize: 12.5 }}>{a.target}</td>
                <td className="mono subtle" style={{ fontSize: 12, maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{Object.keys(a.detail || {}).length ? JSON.stringify(a.detail) : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row" style={{ padding: 12, justifyContent: "flex-end" }}>
        <button className="btn btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Newer</button>
        <button className="btn btn-sm" disabled={!data || offset + 20 >= data.total} onClick={() => setOffset(offset + 20)}>Older</button>
      </div>
    </div>
  );
}
