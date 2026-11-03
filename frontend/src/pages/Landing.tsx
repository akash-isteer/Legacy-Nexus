import { useState } from "react";
import { LuBadgeCheck, LuEyeOff, LuHistory, LuServer } from "react-icons/lu";
import { DemoPlayer } from "../components/DemoPlayer";
import { Logo } from "../components/ui";
import { useAuth } from "../lib/auth";
import { Link } from "../lib/router";

const PROBLEMS = [
  {
    key: "understand",
    title: "Nobody understands the old code",
    sub: "Ask questions, get cited answers",
    panel: (
      <>
        <h3>“Where is interest calculated?”</h3>
        <p style={{ marginBottom: 18 }}>Answers name the program, paragraph, table and field, and cite the exact lines. If the code does not contain the answer, LegacyNexus says so.</p>
        <div className="demo-a" style={{ marginTop: 0 }}>
          Interest calculation happens in <b>INTEREST.CBL</b>, called from <b>ACCOUNT.CBL</b>, using the <b>INTEREST_RATE</b> field from DB2 table <b>ACCOUNT_MASTER</b>.
          <div className="cites"><span>INTEREST.CBL:60-64</span><span>ACCOUNT.CBL:107</span><span>INTEREST.CBL:36</span></div>
        </div>
      </>
    ),
  },
  {
    key: "deps",
    title: "Dependencies are hard to see",
    sub: "Automatic dependency graph",
    panel: (
      <>
        <h3>From job to report, mapped automatically</h3>
        <p>Jobs, programs, tables, datasets, APIs and external calls, every edge backed by a source line.</p>
        <Chain items={["JOB NIGHTLY", "PAYMENT", "ACCOUNT", "ACCOUNT_MASTER", "REPORT12", "REPORT 12"]} />
      </>
    ),
  },
  {
    key: "docs",
    title: "Documentation is missing",
    sub: "Seven document sets, generated",
    panel: (
      <>
        <h3>Documentation without the months of reading</h3>
        <p style={{ marginBottom: 16 }}>Generated from the parsed code, with a source reference on every statement. Download as Markdown.</p>
        <div className="row-wrap">
          {["Application overview", "Module documentation", "API documentation", "Data-flow documentation", "Business-rule documentation", "Database mapping", "Dependency documentation"].map((d) => (
            <span key={d} className="demo-file">{d}</span>
          ))}
        </div>
      </>
    ),
  },
  {
    key: "risk",
    title: "Modernization is risky",
    sub: "Rules and parity tests first",
    panel: (
      <>
        <h3>Will the new system behave exactly like the old one?</h3>
        <p style={{ marginBottom: 16 }}>Every business rule is extracted with its source lines and turned into positive and negative parity scenarios before a line of Java is written.</p>
        <div className="demo-code" style={{ background: "#0b1830", padding: 14, borderRadius: 8 }}>
          <div>Scenario: ACCOUNT – BR-012 condition holds</div>
          <div>  Given WS-ACCOUNT-TYPE = 'SAV' AND WS-NEW-BALANCE &lt; WS-MIN-BALANCE (500.00)</div>
          <div>  Then set LK-RETURN-CODE to 32</div>
        </div>
      </>
    ),
  },
  {
    key: "impact",
    title: "Impact of a change is unknown",
    sub: "Field-level impact analysis",
    panel: (
      <>
        <h3>Change CUSTOMER_ADDRESS. What breaks?</h3>
        <p>Data and control flow are traced through MOVEs, SQL, CALL parameters, files and APIs.</p>
        <Chain items={["CUSTOMER_ADDRESS", "CUSTOMER.CBL", "ACCOUNT.CBL", "PAYMENT.CBL", "REPORT_12", "API_CUSTOMER"]} />
      </>
    ),
  },
];

function Chain({ items }: { items: string[] }) {
  return (
    <div className="row-wrap" style={{ marginTop: 20, gap: 8 }}>
      {items.map((it, i) => (
        <span key={it} className="row" style={{ gap: 8 }}>
          <span className="demo-file">{it}</span>
          {i < items.length - 1 && <span style={{ color: "var(--amber)" }}>→</span>}
        </span>
      ))}
    </div>
  );
}

export function LandingPage() {
  const { user } = useAuth();
  const [tab, setTab] = useState(0);
  const cta = user ? { to: "/projects", label: "Open workspace" } : { to: "/login", label: "Sign in" };

  return (
    <div className="landing">
      <nav className="l-nav">
        <span className="brand"><Logo size={30} /> LegacyNexus AI</span>
        <a className="link" href="#problems">Capabilities</a>
        <a className="link" href="#how">How it works</a>
        <a className="link" href="#trust">Security</a>
        <span className="spacer" />
        <Link to={cta.to} className="btn btn-signal">{cta.label}</Link>
      </nav>

      <header className="l-hero">
        <div>
          <h1>
            <span className="mono-line">IDENTIFICATION DIVISION.</span>
            Understand any legacy application before you change it.
          </h1>
          <p className="lead">
            LegacyNexus reads COBOL, JCL and DB2 code, maps every dependency, extracts the business rules and answers
            questions with line-level citations. Then it plans the modernization.
          </p>
          <div className="ctas">
            <Link to={cta.to} className="btn btn-signal btn-lg">{user ? cta.label : "Sign in to the workspace"}</Link>
            <a href="#problems" className="btn btn-outline btn-lg">See what it does</a>
          </div>
          <div className="l-proof">
            <span><b>0 tokens</b>for parsing, graphs, rules and docs</span>
            <span><b>Line-level</b>citations, verified</span>
            <span><b>6 providers</b>incl. on-prem Ollama</span>
          </div>
        </div>
        <DemoPlayer />
      </header>

      <section className="l-section" id="problems">
        <h2>The five questions every modernization programme starts with</h2>
        <p className="intro">Months of manual analysis usually come before any transformation. LegacyNexus answers these on day one.</p>
        <div className="p-tabs">
          <div className="p-tab-list" role="tablist">
            {PROBLEMS.map((p, i) => (
              <button key={p.key} role="tab" aria-selected={tab === i} className={`p-tab ${tab === i ? "on" : ""}`} onClick={() => setTab(i)}>
                <b>{p.title}</b>
                <span>{p.sub}</span>
              </button>
            ))}
          </div>
          <div className="p-panel" role="tabpanel">{PROBLEMS[tab].panel}</div>
        </div>
      </section>

      <section className="l-section alt" id="how">
        <h2>Deterministic where it can be, AI only where it helps</h2>
        <p className="intro">Parsing and analysis use real parsers and a knowledge graph. The language model is used only to phrase answers to your questions, from a handful of retrieved excerpts.</p>
        <div className="flow">
          {[
            ["Ingest", "Upload COBOL, JCL and DB2, plus Java, C#, Python, JavaScript/TypeScript, shell scripts, YAML config, OpenAPI specs and notes."],
            ["Parse", "Structural COBOL parser, sqlglot for SQL, JCL and API parsers. No tokens spent."],
            ["Connect", "The Legacy Knowledge Graph links code, programs, rules, tables, APIs and capabilities."],
            ["Act", "Ask, map, assess impact, document and plan the migration, all from the same graph."],
          ].map(([t, d], i) => (
            <div key={t} className="flow-step">
              <span className="n">step {i + 1}</span>
              <b>{t}</b>
              <p>{d}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="l-section" id="trust">
        <h2>Built for code you cannot afford to leak or misread</h2>
        <div className="trust">
          <div><LuEyeOff size={26} /><b>No full files to the model</b><p>Only the most relevant excerpts, within a character budget you control.</p></div>
          <div><LuBadgeCheck size={26} /><b>Verified citations</b><p>Cited files, lines and identifiers are checked; unsupported answers abstain.</p></div>
          <div><LuServer size={26} /><b>Your model, your network</b><p>Anthropic, OpenAI, Azure OpenAI, Gemini, any compatible gateway, or local Ollama.</p></div>
          <div><LuHistory size={26} /><b>Audit trail</b><p>Sign-ins, uploads, questions and setting changes are recorded. Keys are encrypted at rest.</p></div>
        </div>
        <div className="l-cta" style={{ marginTop: 64 }}>
          <div>
            <h2 style={{ color: "#fff" }}>See it on a real legacy application</h2>
            <p>Sign in and load the sample core-banking system: it takes about a minute.</p>
          </div>
          <Link to={cta.to} className="btn btn-signal btn-lg">{cta.label}</Link>
        </div>
      </section>

      <footer className="l-foot">
        <span>© {new Date().getFullYear()} LegacyNexus AI</span>
        <span>Legacy application comprehension &amp; modernization</span>
      </footer>
    </div>
  );
}
