import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { LuInbox, LuX } from "react-icons/lu";

/* ---------------- toasts ---------------- */
type Toast = { id: number; text: string; error?: boolean };
const ToastContext = createContext<(text: string, error?: boolean) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, error = false) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, error }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), error ? 6000 : 3500);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toast-wrap" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.error ? "err" : ""}`}>{t.text}</div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

/* ---------------- modal ---------------- */
export function Modal({ title, children, footer, onClose, width }: {
  title: ReactNode; children: ReactNode; footer?: ReactNode; onClose: () => void; width?: number;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" style={width ? { width: `min(${width}px, 100%)` } : undefined}>
        <div className="modal-head row">
          <h2 style={{ flex: 1 }}>{title}</h2>
          <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close"><LuX size={18} /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

/* ---------------- empty & loading ---------------- */
export function EmptyState({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      {icon ?? <LuInbox size={36} />}
      <h3>{title}</h3>
      {children && <p style={{ maxWidth: 460, margin: "0 auto" }}>{children}</p>}
      {action && <div style={{ marginTop: 18 }}>{action}</div>}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="row" style={{ padding: 32, justifyContent: "center", color: "var(--text-2)" }}>
      <span className="spinner" /> {label}…
    </div>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return <div className="error-box">{message}</div>;
}

/* ---------------- confidence ---------------- */
export function ConfidenceRing({ value, label }: { value: number; label: string }) {
  const pct = Math.round(value * 100);
  const r = 18;
  const c = 2 * Math.PI * r;
  const color = label === "High" ? "var(--green)" : label === "Medium" ? "var(--amber-strong)" : "var(--red)";
  return (
    <div className="conf" title={`Confidence ${pct}%`}>
      <div className="conf-ring">
        <svg width="44" height="44" viewBox="0 0 44 44">
          <circle cx="22" cy="22" r={r} fill="none" stroke="var(--line)" strokeWidth="4" />
          <circle cx="22" cy="22" r={r} fill="none" stroke={color} strokeWidth="4" strokeLinecap="round"
            strokeDasharray={c} strokeDashoffset={c * (1 - value)} />
        </svg>
        <b>{pct}</b>
      </div>
      <div style={{ lineHeight: 1.2 }}>
        <div style={{ fontWeight: 600, color }}>{label} confidence</div>
        <div className="subtle">score {value.toFixed(2)}</div>
      </div>
    </div>
  );
}

/* ---------------- code view ---------------- */
const KW = /\b(IDENTIFICATION|ENVIRONMENT|DATA|PROCEDURE|DIVISION|SECTION|PROGRAM-ID|WORKING-STORAGE|LINKAGE|FILE|FD|PIC|VALUE|COPY|PERFORM|CALL|USING|MOVE|TO|IF|ELSE|END-IF|EVALUATE|WHEN|END-EVALUATE|COMPUTE|ADD|SUBTRACT|EXEC|SQL|END-EXEC|SELECT|INSERT|UPDATE|DELETE|FROM|INTO|WHERE|SET|VALUES|CREATE|TABLE|JOIN|ON|AND|OR|NOT|GOBACK|STOP|RUN|OPEN|CLOSE|READ|WRITE|INPUT|OUTPUT|UNTIL|DECLARE|CURSOR|FETCH|JOB|EXEC|DD|DSN|DISP|PGM|PRIMARY|KEY|REFERENCES|BEGIN|END|PROCEDURE)\b/g;

function highlight(line: string) {
  const trimmed = line.trimStart();
  const isComment = (line.length > 6 && line[6] === "*") || trimmed.startsWith("--") || trimmed.startsWith("//*") || trimmed.startsWith("#");
  if (isComment) return [<span key="c" className="tok-cmt">{line}</span>];
  const parts: ReactNode[] = [];
  // Keywords and numbers only count when they are not part of a hyphenated COBOL name (ACCT-OPEN, 1000-MAIN).
  const kw = KW.source.replace(/^\\b/, "(?<![\\w-])").replace(/\\b$/, "(?![\\w-])");
  const re = new RegExp(`('[^']*'|"[^"]*")|${kw}|((?<![\\w-])\\d+(?:\\.\\d+)?(?![\\w-]))`, "g");
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(line))) {
    if (m.index > last) parts.push(line.slice(last, m.index));
    const cls = m[1] ? "tok-str" : m[3] ? "tok-num" : "tok-kw";
    parts.push(<span key={i++} className={cls}>{m[0]}</span>);
    last = m.index + m[0].length;
  }
  if (last < line.length) parts.push(line.slice(last));
  return parts;
}

export function CodeView({ lines, start = 1, highlightFrom, highlightTo, inline, scrollTo = true }: {
  lines: string[]; start?: number; highlightFrom?: number; highlightTo?: number; inline?: boolean; scrollTo?: boolean;
}) {
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!el || !scrollTo || !highlightFrom) return;
    const target = el.querySelector<HTMLElement>(`[data-ln="${highlightFrom}"]`);
    if (target) el.scrollTop = Math.max(0, target.offsetTop - 80);
  }, [el, highlightFrom, scrollTo, lines]);
  return (
    <div className={`code ${inline ? "inline" : ""}`} ref={setEl}>
      {lines.map((ln, i) => {
        const n = start + i;
        const hl = highlightFrom !== undefined && n >= highlightFrom && n <= (highlightTo ?? highlightFrom);
        return (
          <div key={n} className={`ln ${hl ? "hl" : ""}`} data-ln={n}>
            <span className="no">{n}</span>
            <span className="tx">{highlight(ln)}</span>
          </div>
        );
      })}
    </div>
  );
}

/* ---------------- logo ---------------- */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" fill="#183257" />
      <g fill="#E9A23B">
        <rect x="6" y="7" width="9" height="3" rx="1" />
        <rect x="6" y="13" width="6" height="3" rx="1" />
        <rect x="6" y="19" width="9" height="3" rx="1" />
      </g>
      <path d="M15 8.5h4.5l3 7.5-3 7.5H15" stroke="#8FD3CC" strokeWidth="2" fill="none" />
      <circle cx="23" cy="16" r="3" fill="#8FD3CC" />
    </svg>
  );
}

export function TypeDot({ type }: { type: string }) {
  return <span className="dot" style={{ background: `var(--t-${type}, var(--text-3))` }} />;
}
