import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { LuArrowLeft, LuCircleCheck, LuEye, LuEyeOff, LuLock, LuMail, LuShieldCheck, LuUserPlus } from "react-icons/lu";
import { Logo } from "../components/ui";
import { api } from "../lib/api";
import { SIGNIN_ROLES, useAuth } from "../lib/auth";
import { Link, navigate, useSearchParam } from "../lib/router";
import type { DocRole } from "../lib/types";

type Options = { allow_signup: boolean; email_enabled: boolean; password_rule: string };

function useAuthOptions() {
  const [opts, setOpts] = useState<Options>({ allow_signup: true, email_enabled: false, password_rule: "" });
  useEffect(() => {
    api.get<Options>("/auth/options").then(setOpts).catch(() => {});
  }, []);
  return opts;
}

/** Two-column layout shared by sign-in, sign-up and password pages. */
function AuthLayout({ title, subtitle, children, footer }: { title: string; subtitle: string; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="auth-grid">
      <section className="auth-brand">
        <Link to="/" className="row" style={{ color: "#fff", fontWeight: 600, fontSize: 17, textDecoration: "none" }}>
          <Logo size={32} /> LegacyNexus AI
        </Link>
        <div className="auth-pitch">
          <h1>Understand any application before you change it.</h1>
          <p>Every program, table, rule and dependency, mapped from the code and explained in plain language, for engineers and business owners alike.</p>
          <ul>
            <li><LuCircleCheck size={16} /> COBOL, Java, .NET, Python, JavaScript and more</li>
            <li><LuCircleCheck size={16} /> Business rules your teams can read and approve</li>
            <li><LuCircleCheck size={16} /> Every answer linked to the exact line of code</li>
          </ul>
        </div>
        <div className="auth-foot"><LuShieldCheck size={15} /> Your code stays in your environment and is never used to train AI models.</div>
      </section>
      <section className="auth-form-wrap">
        <div className="auth-card">
          <div className="auth-mobile-logo"><Logo size={36} /></div>
          <h1>{title}</h1>
          <p className="muted" style={{ marginTop: 6, marginBottom: 22 }}>{subtitle}</p>
          {children}
          {footer && <div className="auth-switch">{footer}</div>}
        </div>
      </section>
    </div>
  );
}

function PasswordInput({ id, value, onChange, autoComplete, label = "Password" }: {
  id: string; value: string; onChange: (v: string) => void; autoComplete: string; label?: string;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="input-affix">
        <input id={id} className="input" type={show ? "text" : "password"} autoComplete={autoComplete} required
          value={value} onChange={(e) => onChange(e.target.value)} />
        <button type="button" className="affix" onClick={() => setShow((s) => !s)} aria-label={show ? "Hide password" : "Show password"}>
          {show ? <LuEyeOff size={16} /> : <LuEye size={16} />}
        </button>
      </div>
    </div>
  );
}

/** "Sign in as" picker: the role decides how Document Q&A answers are written. */
function RolePicker({ value, onChange }: { value: DocRole; onChange: (r: DocRole) => void }) {
  return (
    <div className="field">
      <label htmlFor="signin-role">Sign in as</label>
      <select id="signin-role" className="input" value={value} onChange={(e) => onChange(e.target.value as DocRole)}>
        {SIGNIN_ROLES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
      </select>
      <span className="hint">{SIGNIN_ROLES.find((r) => r.id === value)?.hint}. Document Q&amp;A answers are written for this role.</span>
    </div>
  );
}

function strength(pw: string): { score: number; label: string } {
  let s = 0;
  if (pw.length >= 8) s++;
  if (pw.length >= 12) s++;
  if (/[A-Za-z]/.test(pw) && /\d/.test(pw)) s++;
  if (/[^A-Za-z0-9]/.test(pw) || /[A-Z]/.test(pw) && /[a-z]/.test(pw)) s++;
  return { score: s, label: ["Too short", "Weak", "Fair", "Good", "Strong"][s] };
}

function StrengthMeter({ password }: { password: string }) {
  if (!password) return null;
  const { score, label } = strength(password);
  return (
    <div className="strength" aria-live="polite">
      <div className="bars">{[0, 1, 2, 3].map((i) => <span key={i} className={i < score ? `on s${score}` : ""} />)}</div>
      <span className="subtle">{label}</span>
    </div>
  );
}

export function LoginPage() {
  const { login, user, signinRole } = useAuth();
  const opts = useAuthOptions();
  const [role, setRole] = useState<DocRole>(signinRole);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [demo, setDemo] = useState<string | null>(null);
  const changed = useSearchParam("reset") === "done";

  useEffect(() => {
    if (user) navigate("/projects", true);
  }, [user]);
  useEffect(() => {
    api.get<{ demo_login: string | null }>("/health").then((h) => setDemo(h.demo_login)).catch(() => {});
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password, role);
      navigate("/projects", true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout title="Sign in" subtitle="Welcome back. Sign in to your workspace."
      footer={opts.allow_signup && <>New to LegacyNexus? <Link to="/signup">Create an account</Link></>}>
      <form onSubmit={submit} className="stack">
        {changed && <div className="info-box"><LuCircleCheck size={15} style={{ verticalAlign: -2 }} /> Password changed. Sign in with your new password.</div>}
        {demo && (
          <div className="info-box" style={{ fontSize: 13 }}>
            Demo environment: <b className="mono" style={{ cursor: "pointer" }} onClick={() => setEmail(demo)}>{demo}</b> (password in <span className="mono">backend/.env</span>).
          </div>
        )}
        <div className="field">
          <label htmlFor="email">Work email</label>
          <input id="email" className="input" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <PasswordInput id="password" value={password} onChange={setPassword} autoComplete="current-password" />
        <RolePicker value={role} onChange={setRole} />
        <div className="row" style={{ marginTop: -6 }}>
          <span className="spacer" />
          <Link to="/forgot-password" style={{ fontSize: 13.5 }}>Forgot password?</Link>
        </div>
        {error && <div className="error-box" role="alert">{error}</div>}
        <button className="btn btn-primary btn-lg" type="submit" disabled={busy}>
          {busy ? <span className="spinner" /> : <LuLock size={16} />} Sign in
        </button>
      </form>
    </AuthLayout>
  );
}

export function SignupPage() {
  const { register, user, signinRole } = useAuth();
  const opts = useAuthOptions();
  const [role, setRole] = useState<DocRole>(signinRole);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [agree, setAgree] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (user) navigate("/projects", true);
  }, [user]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (password !== confirm) return setError("The two passwords do not match.");
    if (strength(password).score < 2) return setError(opts.password_rule || "Choose a stronger password.");
    setBusy(true);
    try {
      await register(name.trim(), email.trim(), password, role);
      navigate("/projects", true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!opts.allow_signup) {
    return (
      <AuthLayout title="Accounts are by invitation" subtitle="Self sign-up is turned off for this workspace."
        footer={<Link to="/login"><LuArrowLeft size={14} style={{ verticalAlign: -2 }} /> Back to sign in</Link>}>
        <div className="info-box">Ask your LegacyNexus administrator to create an account for you.</div>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Create your account" subtitle="Start analysing applications in minutes."
      footer={<>Already have an account? <Link to="/login">Sign in</Link></>}>
      <form onSubmit={submit} className="stack">
        <div className="field">
          <label htmlFor="name">Full name</label>
          <input id="name" className="input" autoComplete="name" required value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="email">Work email</label>
          <input id="email" className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <PasswordInput id="password" value={password} onChange={setPassword} autoComplete="new-password" />
          <StrengthMeter password={password} />
          <span className="hint">{opts.password_rule}</span>
        </div>
        <PasswordInput id="confirm" label="Confirm password" value={confirm} onChange={setConfirm} autoComplete="new-password" />
        <RolePicker value={role} onChange={setRole} />
        <label className="check" style={{ fontSize: 13.5 }}>
          <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} required />
          I will only upload code I am authorised to analyse.
        </label>
        {error && <div className="error-box" role="alert">{error}</div>}
        <button className="btn btn-primary btn-lg" type="submit" disabled={busy || !agree}>
          {busy ? <span className="spinner" /> : <LuUserPlus size={16} />} Create account
        </button>
      </form>
    </AuthLayout>
  );
}

export function ForgotPasswordPage() {
  const opts = useAuthOptions();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ message: string; dev_reset_url?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setDone(await api.post<{ message: string; dev_reset_url?: string }>("/auth/forgot-password", { email: email.trim() }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout title="Reset your password" subtitle="Enter your email and we will send you a link to choose a new password."
      footer={<Link to="/login"><LuArrowLeft size={14} style={{ verticalAlign: -2 }} /> Back to sign in</Link>}>
      {done ? (
        <div className="stack">
          <div className="info-box"><LuMail size={15} style={{ verticalAlign: -2 }} /> {done.message}</div>
          {done.dev_reset_url && (
            <div className="warn-box" style={{ fontSize: 13.5 }}>
              Email is not configured on this server (development mode), so here is the link directly:<br />
              <a href={done.dev_reset_url.replace(/^https?:\/\/[^/]+/, "")}>Open the password reset page</a>
            </div>
          )}
          {!opts.email_enabled && !done.dev_reset_url && (
            <p className="subtle">No email arriving? Your administrator can create a reset link for you from Settings → Users.</p>
          )}
        </div>
      ) : (
        <form onSubmit={submit} className="stack">
          <div className="field">
            <label htmlFor="email">Work email</label>
            <input id="email" className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          {error && <div className="error-box" role="alert">{error}</div>}
          <button className="btn btn-primary btn-lg" type="submit" disabled={busy}>
            {busy ? <span className="spinner" /> : <LuMail size={16} />} Send reset link
          </button>
        </form>
      )}
    </AuthLayout>
  );
}

export function ResetPasswordPage() {
  const token = useSearchParam("token") ?? "";
  const opts = useAuthOptions();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (password !== confirm) return setError("The two passwords do not match.");
    setBusy(true);
    try {
      await api.post("/auth/reset-password", { token, password });
      navigate("/login?reset=done", true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout title="Choose a new password" subtitle="The link works once and expires after a short time."
      footer={<Link to="/forgot-password">Need a new link?</Link>}>
      {!token ? (
        <div className="error-box">This page needs the link from your reset email.</div>
      ) : (
        <form onSubmit={submit} className="stack">
          <div>
            <PasswordInput id="password" label="New password" value={password} onChange={setPassword} autoComplete="new-password" />
            <StrengthMeter password={password} />
            <span className="hint">{opts.password_rule}</span>
          </div>
          <PasswordInput id="confirm" label="Confirm new password" value={confirm} onChange={setConfirm} autoComplete="new-password" />
          {error && <div className="error-box" role="alert">{error}</div>}
          <button className="btn btn-primary btn-lg" type="submit" disabled={busy}>
            {busy ? <span className="spinner" /> : <LuLock size={16} />} Save new password
          </button>
        </form>
      )}
    </AuthLayout>
  );
}
