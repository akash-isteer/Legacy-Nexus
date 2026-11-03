import { useEffect, useRef, useState } from "react";
import { LuFileCode2, LuPause, LuPlay } from "react-icons/lu";

/* A scripted, scrubbable walkthrough of the product, rendered live (no video file needed). */

const SCENES = [
  { key: "upload", label: "Upload", dur: 4.5 },
  { key: "parse", label: "Parse", dur: 5 },
  { key: "graph", label: "Map", dur: 5.5 },
  { key: "ask", label: "Ask", dur: 6 },
  { key: "impact", label: "Impact", dur: 5 },
] as const;
const STARTS = SCENES.reduce<number[]>((acc, _s, i) => [...acc, i === 0 ? 0 : acc[i - 1] + SCENES[i - 1].dur], []);
const TOTAL = SCENES.reduce((a, s) => a + s.dur, 0);

const clamp = (v: number, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const ease = (v: number) => 1 - Math.pow(1 - clamp(v), 3);

export function DemoPlayer() {
  const video = import.meta.env.VITE_DEMO_VIDEO_URL as string | undefined;
  const reduced = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const [t, setT] = useState(reduced ? STARTS[2] + 5 : 0);
  const [playing, setPlaying] = useState(!reduced);
  const last = useRef<number | null>(null);

  useEffect(() => {
    if (!playing) {
      last.current = null;
      return;
    }
    let raf = 0;
    const tick = (now: number) => {
      if (last.current !== null) setT((v) => (v + (now - last.current!) / 1000) % TOTAL);
      last.current = now;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  if (video) {
    return (
      <div className="demo">
        <video src={video} autoPlay muted loop playsInline controls style={{ width: "100%", display: "block" }} />
      </div>
    );
  }

  const sceneIndex = Math.max(0, STARTS.findIndex((s, i) => t >= s && t < s + SCENES[i].dur));
  const local = (i: number) => t - STARTS[i];

  return (
    <div className="demo" aria-label="Product demo">
      <div className="demo-chrome">
        <i /><i /><i />
        <span>legacynexus · Core Banking (sample)</span>
      </div>
      <div className="demo-stage">
        {SCENES.map((s, i) => (
          <div key={s.key} className="demo-scene" style={{ opacity: sceneIndex === i ? 1 : 0, pointerEvents: sceneIndex === i ? "auto" : "none" }} aria-hidden={sceneIndex !== i}>
            {s.key === "upload" && <UploadScene lt={local(i)} />}
            {s.key === "parse" && <ParseScene lt={local(i)} />}
            {s.key === "graph" && <GraphScene lt={local(i)} />}
            {s.key === "ask" && <AskScene lt={local(i)} />}
            {s.key === "impact" && <ImpactScene lt={local(i)} />}
          </div>
        ))}
      </div>
      <div className="demo-controls">
        <button className="play" onClick={() => setPlaying((p) => !p)} aria-label={playing ? "Pause demo" : "Play demo"}>
          {playing ? <LuPause size={15} /> : <LuPlay size={15} />}
        </button>
        <div className="demo-track">
          {SCENES.map((s, i) => {
            const p = sceneIndex > i ? 1 : sceneIndex === i ? local(i) / s.dur : 0;
            return (
              <button key={s.key} className={sceneIndex === i ? "on" : ""} onClick={() => setT(STARTS[i] + 0.01)}>
                <div className="bar"><span style={{ width: `${p * 100}%` }} /></div>
                {s.label}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Caption({ title, text }: { title: string; text: string }) {
  return <div className="demo-caption"><b>{title}</b> — {text}</div>;
}

const FILES = ["CUSTOMER.CBL", "ACCOUNT.CBL", "INTEREST.CBL", "PAYMENT.CBL", "REPORT12.CBL", "CUSTREC.cpy", "NIGHTLY.jcl", "bank_schema.sql", "customer_api.yaml"];

function UploadScene({ lt }: { lt: number }) {
  const parsed = Math.floor(clamp((lt - 2.6) / 1.4) * 2000);
  return (
    <>
      <div style={{ border: "2px dashed #2b4670", borderRadius: 12, padding: 20, height: 250, display: "flex", flexWrap: "wrap", gap: 10, alignContent: "flex-start" }}>
        {FILES.map((f, i) => {
          const p = ease((lt - 0.2 - i * 0.22) / 0.5);
          return (
            <span key={f} className="demo-file" style={{ opacity: p, transform: `translateY(${(1 - p) * -30}px)` }}>
              <LuFileCode2 size={13} color="#8fd3cc" /> {f}
            </span>
          );
        })}
        <div style={{ width: "100%", marginTop: 18, color: "#9fb3cf", fontSize: 13, opacity: clamp(lt - 2.4) }}>
          <div style={{ height: 4, background: "#1d3a64", borderRadius: 2, overflow: "hidden", marginBottom: 8 }}>
            <span style={{ display: "block", height: "100%", width: `${clamp((lt - 2.6) / 1.4) * 100}%`, background: "#e9a23b" }} />
          </div>
          Inventory · {parsed.toLocaleString()} programs · 500 tables · 300 procedures
        </div>
      </div>
      <Caption title="Upload" text="COBOL, copybooks, JCL, DB2 DDL, APIs and docs, as files or a zip." />
    </>
  );
}

const CODE = [
  "       2000-VALIDATE-HOLDER.",
  "           CALL 'CUSTOMER' USING WS-CUSTOMER-ID",
  "                                 WS-CUSTOMER-REC WS-CUST-RC",
  "       3100-UPDATE-BALANCE.",
  "           EXEC SQL",
  "               UPDATE ACCOUNT_MASTER",
  "                  SET BALANCE = :WS-NEW-BALANCE",
  "           END-EXEC.",
  "       4000-ACCRUE-INTEREST.",
  "           CALL 'INTEREST' USING WS-ACCOUNT-ID",
];
const FINDINGS: Record<number, string> = { 1: "CALLS → CUSTOMER", 5: "WRITES → ACCOUNT_MASTER.BALANCE", 9: "CALLS → INTEREST" };

function ParseScene({ lt }: { lt: number }) {
  const cursor = Math.floor(clamp(lt / 4) * CODE.length);
  return (
    <>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.45fr) minmax(0, 1fr)", gap: 16 }}>
        <div className="demo-code" style={{ overflow: "hidden", fontSize: 11.5 }}>
          {CODE.map((ln, i) => (
            <div key={i} className={i === cursor ? "hit" : ""} style={{ whiteSpace: "pre", opacity: i <= cursor ? 1 : 0.45 }}>
              <span style={{ color: "#4d6488", marginRight: 10 }}>{String(66 + i).padStart(3)}</span>{ln}
            </div>
          ))}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, paddingTop: 6 }}>
          <div style={{ fontSize: 12, color: "#8ea2bf" }}>Deterministic parser · no AI tokens</div>
          {Object.entries(FINDINGS).map(([line, text]) =>
            cursor >= Number(line) ? (
              <span key={line} className="demo-file" style={{ color: "#8fd3cc", flexDirection: "column", alignItems: "flex-start", gap: 2 }}>{text}<span style={{ color: "#6f84a3" }}>ACCOUNT.CBL:{66 + Number(line)}</span></span>
            ) : null,
          )}
        </div>
      </div>
      <Caption title="Parse" text="Paragraphs, CALLs, SQL, files and rules are read with real parsers, line by line." />
    </>
  );
}

const G_NODES = [
  { id: "NIGHTLY", x: 40, y: 120, c: "#6352c6", at: 0.2 },
  { id: "PAYMENT", x: 150, y: 120, c: "#2c67a8", at: 0.6 },
  { id: "ACCOUNT", x: 265, y: 60, c: "#2c67a8", at: 1.0 },
  { id: "INTEREST", x: 385, y: 30, c: "#2c67a8", at: 1.4 },
  { id: "ACCOUNT_MASTER", x: 385, y: 95, c: "#15807a", at: 1.7 },
  { id: "PAYMENT_LEDGER", x: 265, y: 185, c: "#15807a", at: 2.0 },
  { id: "REPORT12", x: 385, y: 185, c: "#2c67a8", at: 2.4 },
  { id: "REPORT 12", x: 500, y: 185, c: "#a8701a", at: 2.8 },
];
const G_EDGES = [["NIGHTLY", "PAYMENT"], ["PAYMENT", "ACCOUNT"], ["ACCOUNT", "INTEREST"], ["ACCOUNT", "ACCOUNT_MASTER"], ["PAYMENT", "PAYMENT_LEDGER"], ["PAYMENT_LEDGER", "REPORT12"], ["REPORT12", "REPORT 12"]];

function MiniGraph({ lt, accent }: { lt: number; accent?: (id: string) => string | null }) {
  const pos = Object.fromEntries(G_NODES.map((n) => [n.id, n]));
  return (
    <svg viewBox="0 0 560 230" style={{ width: "100%", height: 250 }}>
      {G_EDGES.map(([a, b]) => {
        const A = pos[a], B = pos[b];
        const p = ease((lt - B.at + 0.1) / 0.5);
        const x2 = A.x + (B.x - A.x) * p, y2 = A.y + (B.y - A.y) * p;
        return <line key={a + b} x1={A.x} y1={A.y} x2={x2} y2={y2} stroke="#4d6488" strokeWidth={1.6} />;
      })}
      {G_NODES.map((n) => {
        const p = ease((lt - n.at) / 0.4);
        const ring = accent?.(n.id);
        const w = Math.max(70, n.id.length * 7 + 18);
        return (
          <g key={n.id} opacity={p} transform={`translate(${n.x - w / 2}, ${n.y - 14})`}>
            <rect width={w} height={28} rx={7} fill="#13284a" stroke={ring ?? n.c} strokeWidth={ring ? 3 : 2} />
            <text x={w / 2} y={18} textAnchor="middle" fill="#e6edf6" fontSize="11" fontFamily="IBM Plex Sans, sans-serif">{n.id}</text>
          </g>
        );
      })}
    </svg>
  );
}

function GraphScene({ lt }: { lt: number }) {
  return (
    <>
      <MiniGraph lt={lt} />
      <Caption title="Map" text="Job → program → program → table → program → report, built automatically." />
    </>
  );
}

const QUESTION = "Where is interest calculated?";

function AskScene({ lt }: { lt: number }) {
  const typed = QUESTION.slice(0, Math.floor(clamp(lt / 1.6) * QUESTION.length));
  const show = clamp((lt - 2.0) / 0.5);
  const conf = clamp((lt - 2.6) / 1.4) * 0.91;
  return (
    <>
      <div style={{ textAlign: "right" }}><span className="demo-q">{typed || " "}</span></div>
      <div className="demo-a" style={{ opacity: show }}>
        Interest is calculated in <b>INTEREST.CBL</b>, paragraph 2000-CALC-INTEREST, called from <b>ACCOUNT.CBL</b>,
        using the <b>INTEREST_RATE</b> field from DB2 table <b>ACCOUNT_MASTER</b>.
        <div className="cites"><span>INTEREST.CBL:60-64</span><span>ACCOUNT.CBL:107</span><span>INTEREST.CBL:36-42</span></div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 12, fontSize: 12, color: "#9fb3cf" }}>
          Confidence
          <div style={{ flex: 1, height: 5, background: "#1d3a64", borderRadius: 3, overflow: "hidden", maxWidth: 200 }}>
            <span style={{ display: "block", height: "100%", width: `${conf * 100}%`, background: "#2a8253" }} />
          </div>
          <b style={{ color: "#fff" }}>{conf.toFixed(2)}</b> · 3/3 citations verified
        </div>
      </div>
      <Caption title="Ask" text="Grounded answers with verified file and line citations — or “Not found”." />
    </>
  );
}

const CHAIN = ["CUSTOMER_ADDRESS", "CUSTOMER.CBL", "ACCOUNT.CBL", "PAYMENT.CBL", "REPORT_12", "API_CUSTOMER"];

function ImpactScene({ lt }: { lt: number }) {
  return (
    <>
      <div style={{ display: "flex", flexDirection: "column", gap: 9, alignItems: "flex-start", paddingLeft: 30 }}>
        {CHAIN.map((c, i) => {
          const p = ease((lt - 0.3 - i * 0.45) / 0.4);
          const color = i === 0 ? "#e9a23b" : i < 3 ? "#e0634e" : "#e9a23b";
          return (
            <div key={c} style={{ opacity: p, transform: `translateX(${(1 - p) * -20}px)`, display: "flex", alignItems: "center", gap: 10 }}>
              {i > 0 && <span style={{ color: "#4d6488", width: 14, textAlign: "center" }}>↓</span>}
              {i === 0 && <span style={{ width: 14 }} />}
              <span className="demo-file" style={{ borderColor: color, color: i === 0 ? "#e9a23b" : "#e6edf6", marginLeft: i * 14 }}>{c}</span>
              {i > 0 && <span style={{ fontSize: 11, color }}>{i < 3 ? "High" : "Medium"}</span>}
            </div>
          );
        })}
      </div>
      <Caption title="Impact" text="Change a column and see exactly what can break, with evidence for every hop." />
    </>
  );
}
