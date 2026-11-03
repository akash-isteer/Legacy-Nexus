import { useMemo, useState } from "react";
import {
  LuArrowRight, LuBoxes, LuBriefcaseBusiness, LuCode, LuCopy, LuDatabase, LuFileCode2, LuFiles, LuFolder,
  LuFolderGit2, LuHardDrive, LuPlug, LuScrollText, LuSearch, LuServer, LuSquareFunction, LuTable2, LuWorkflow,
} from "react-icons/lu";
import { criticalityTag, fmtNumber, TYPE_COLORS } from "../lib/format";
import type { Capability, Overview, OverviewResponse, ProgramStats } from "../lib/types";

export type RepoFile = { path: string; language: string | null; size_bytes: number; line_count: number | null };

// Validated categorical order (blue, orange, aqua, yellow, magenta, green, violet, red); 9th+ folds into "Other".
const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
const OTHER = "#98a2b3";

// Criticality uses the reserved status colours, always shown with its label.
export const LEVELS = ["Critical", "High", "Medium", "Low", "Unused"] as const;
const LEVEL_COLOR: Record<string, string> = { Critical: "#d03b3b", High: "#ec835a", Medium: "#fab219", Low: "#0ca30c", Unused: "#c0c6cf" };

export function fmtBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

const pct = (part: number, whole: number) => (whole ? Math.round((part / whole) * 100) : 0);

/** One horizontal bar split into labelled segments, with a legend underneath. */
function SegmentBar({ parts }: { parts: { label: string; value: number; color: string; note?: string }[] }) {
  const total = parts.reduce((s, p) => s + p.value, 0) || 1;
  const shown = parts.filter((p) => p.value > 0);
  return (
    <div>
      <div className="seg-bar" role="img" aria-label={shown.map((p) => `${p.label} ${p.value}`).join(", ")}>
        {shown.map((p) => (
          <span key={p.label} style={{ width: `${(p.value / total) * 100}%`, background: p.color }}
                title={`${p.label}: ${fmtNumber(p.value)} (${pct(p.value, total)}%)${p.note ? ` · ${p.note}` : ""}`} />
        ))}
      </div>
      <div className="seg-legend">
        {shown.map((p) => (
          <span key={p.label}><i style={{ background: p.color }} />{p.label} <b>{fmtNumber(p.value)}</b><em>{pct(p.value, total)}%</em></span>
        ))}
      </div>
    </div>
  );
}

function Fact({ icon: Icon, value, label }: { icon: typeof LuFiles; value: string; label: string }) {
  return (
    <div className="fact">
      <Icon size={16} />
      <div><b>{value}</b><span>{label}</span></div>
    </div>
  );
}

/* --------------------------------------------------------------------------- application map */

export function ApplicationMapCard({ caps, onOpen }: { caps: Capability[]; onOpen: () => void }) {
  return (
    <div className="card" data-tour="app-map">
      <div className="card-head">
        <h3><LuWorkflow size={17} className="h-ico" /> Application map</h3>
        <span className="subtle">Business capabilities found in the code, in the order they depend on each other</span>
      </div>
      <div className="card-body">
        {caps.length === 0 ? <p className="subtle">No business capabilities were identified.</p> : (
          <div className="capflow">
            {caps.map((c, i) => (
              <div key={c.name} className="capflow-item">
                {i > 0 && <span className="capflow-arrow" aria-hidden><LuArrowRight size={18} /></span>}
                <button className={`capcard ${c.unused ? "retired" : ""}`} onClick={onOpen} title="Open the dependency map">
                  <div className="capcard-top">
                    <span className="capcard-n">{i + 1}</span>
                    <LuBriefcaseBusiness size={16} />
                    {c.unused && <span className="tag">Unused</span>}
                  </div>
                  <b>{c.name}</b>
                  <div className="capcard-metrics">
                    <span><b>{c.programs.length}</b>programs</span>
                    <span><b>{c.tables.length}</b>tables</span>
                    <span><b>{c.business_rules}</b>rules</span>
                  </div>
                  <div className="capcard-chips">
                    {c.programs.slice(0, 3).map((p) => <span key={p} className="mono">{p}</span>)}
                    {c.programs.length > 3 && <span>+{c.programs.length - 3}</span>}
                  </div>
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------- repository */

export function RepositoryCard({ files, overview, onOpen }: { files: RepoFile[] | null; overview: Overview; onOpen: (f: string, l: number) => void }) {
  const stats = useMemo(() => {
    const list = files ?? [];
    const bytes = list.reduce((s, f) => s + (f.size_bytes || 0), 0);
    const lines = list.reduce((s, f) => s + (f.line_count || 0), 0);
    const byLang = new Map<string, { files: number; lines: number }>();
    const byFolder = new Map<string, number>();
    for (const f of list) {
      const lang = f.language || "Other";
      const cur = byLang.get(lang) ?? { files: 0, lines: 0 };
      cur.files += 1;
      cur.lines += f.line_count || 0;
      byLang.set(lang, cur);
      const folder = f.path.includes("/") ? f.path.split("/").slice(0, -1).join("/") : "(root)";
      byFolder.set(folder, (byFolder.get(folder) ?? 0) + 1);
    }
    const langs = [...byLang.entries()].sort((a, b) => b[1].files - a[1].files);
    const folders = [...byFolder.entries()].sort((a, b) => b[1] - a[1]);
    const largest = [...list].sort((a, b) => (b.line_count || 0) - (a.line_count || 0)).slice(0, 5);
    return { bytes, lines, langs, folders, largest };
  }, [files]);

  // Fall back to the analysis counts while the file list loads.
  const langParts: { label: string; value: number; color: string; note?: string }[] = stats.langs.length
    ? stats.langs.slice(0, 8).map(([l, v], i) => ({ label: l, value: v.files, color: SERIES[i], note: `${fmtNumber(v.lines)} lines` }))
    : Object.entries(overview.languages).slice(0, 8).map(([l, n], i) => ({ label: l, value: n, color: SERIES[i] }));
  const rest = stats.langs.slice(8).reduce((s, [, v]) => s + v.files, 0);
  if (rest) langParts.push({ label: "Other", value: rest, color: OTHER, note: "" });
  const maxFolder = stats.folders[0]?.[1] ?? 1;

  return (
    <div className="card" data-tour="repo">
      <div className="card-head"><h3><LuFolderGit2 size={17} className="h-ico" /> Repository details</h3><span className="subtle">What was uploaded</span></div>
      <div className="card-body stack" style={{ gap: 18 }}>
        <div className="facts">
          <Fact icon={LuFiles} value={fmtNumber(files?.length ?? overview.files)} label="Files" />
          <Fact icon={LuCode} value={fmtNumber(stats.lines || overview.lines_of_code)} label="Lines" />
          <Fact icon={LuHardDrive} value={files ? fmtBytes(stats.bytes) : "—"} label="Size on disk" />
          <Fact icon={LuFolder} value={files ? fmtNumber(stats.folders.length) : "—"} label="Folders" />
        </div>
        <div>
          <div className="mini-h">Files by language</div>
          <SegmentBar parts={langParts} />
        </div>
        {stats.folders.length > 0 && (
          <div>
            <div className="mini-h">Biggest folders</div>
            <div className="hbars">
              {stats.folders.slice(0, 5).map(([name, n]) => (
                <div key={name} className="hbar" title={`${name}: ${n} files`}>
                  <span className="mono name">{name}</span>
                  <span className="track"><span style={{ width: `${(n / maxFolder) * 100}%` }} /></span>
                  <b>{n}</b>
                </div>
              ))}
            </div>
          </div>
        )}
        {stats.largest.length > 0 && (
          <div>
            <div className="mini-h">Largest files</div>
            <div className="file-list">
              {stats.largest.map((f) => (
                <button key={f.path} onClick={() => onOpen(f.path, 1)} title="Open the source">
                  <LuFileCode2 size={15} />
                  <span className="mono">{f.path}</span>
                  <em>{fmtNumber(f.line_count)} lines</em>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------- code */

export function CodeCard({ data, onOpen }: { data: OverviewResponse; onOpen: (f: string, l: number) => void }) {
  const progs = data.programs;
  const n = progs.length || 1;
  const totalLoc = progs.reduce((s, p) => s + p.loc, 0);
  const avgCc = progs.reduce((s, p) => s + p.complexity, 0) / n;
  const maxCc = Math.max(1, ...progs.map((p) => p.complexity));
  const levels = LEVELS.map((l) => ({ label: l, value: progs.filter((p) => p.criticality === l).length, color: LEVEL_COLOR[l] }));
  const complex = [...progs].sort((a, b) => b.complexity - a.complexity).slice(0, 5);
  const hard = progs.filter((p) => p.complexity >= 20).length;

  return (
    <div className="card" data-tour="code">
      <div className="card-head"><h3><LuCode size={17} className="h-ico" /> Code details</h3><span className="subtle">Size, complexity and risk</span></div>
      <div className="card-body stack" style={{ gap: 18 }}>
        <div className="facts">
          <Fact icon={LuFileCode2} value={fmtNumber(Math.round(totalLoc / n))} label="Avg lines / program" />
          <Fact icon={LuSquareFunction} value={avgCc.toFixed(1)} label="Avg complexity" />
          <Fact icon={LuSquareFunction} value={fmtNumber(maxCc)} label="Highest complexity" />
          <Fact icon={LuScrollText} value={fmtNumber(hard)} label="Hard to change (20+)" />
        </div>
        <div>
          <div className="mini-h">Programs by criticality</div>
          <SegmentBar parts={levels} />
        </div>
        <div>
          <div className="mini-h">Most complex programs <span className="subtle">(decision points in the code)</span></div>
          <div className="hbars">
            {complex.map((p) => (
              <button key={p.program} className="hbar clickable" onClick={() => onOpen(p.file, 1)} title={`${p.program}: complexity ${p.complexity}, ${p.loc} lines`}>
                <span className="mono name">{p.program}</span>
                <span className="track"><span style={{ width: `${(p.complexity / maxCc) * 100}%` }} /></span>
                <b>{p.complexity}</b>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------- inventory */

export function InventoryCard({ o, data }: { o: Overview; data: OverviewResponse }) {
  const items = [
    { key: "program", label: "Programs", n: o.programs_analysed, icon: LuFileCode2 },
    { key: "copybook", label: "Copybooks", n: o.copybooks, icon: LuCopy },
    { key: "job", label: "Batch jobs", n: o.jobs, icon: LuServer },
    { key: "table", label: "DB tables", n: o.tables, icon: LuTable2 },
    { key: "procedure", label: "Stored procedures", n: o.procedures, icon: LuDatabase },
    { key: "dataset", label: "Datasets / reports", n: o.datasets, icon: LuBoxes },
    { key: "api", label: "External interfaces", n: o.external_interfaces, icon: LuPlug },
    { key: "config", label: "Business capabilities", n: data.capabilities.length, icon: LuWorkflow },
  ];
  return (
    <div className="card" data-tour="inventory">
      <div className="card-head"><h3><LuBoxes size={17} className="h-ico" /> Components</h3><span className="subtle">Everything the analysis catalogued</span></div>
      <div className="card-body">
        <div className="inv-grid">
          {items.map((it) => (
            <div key={it.label} className="inv">
              <span className="inv-ico" style={{ color: TYPE_COLORS[it.key], background: `${TYPE_COLORS[it.key]}14` }}><it.icon size={18} /></span>
              <div><b>{fmtNumber(it.n)}</b><span>{it.label}</span></div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------- programs */

type SortKey = "criticality" | "loc" | "complexity" | "fan_in";
const SORTS: { key: SortKey; label: string }[] = [
  { key: "criticality", label: "Risk" }, { key: "loc", label: "Size" }, { key: "complexity", label: "Complexity" }, { key: "fan_in", label: "Callers" },
];

export function ProgramsCard({ programs, onOpen }: { programs: ProgramStats[]; onOpen: (f: string, l: number) => void }) {
  const [q, setQ] = useState("");
  const [level, setLevel] = useState<string>("");
  const [sort, setSort] = useState<SortKey>("criticality");
  const maxCc = Math.max(1, ...programs.map((p) => p.complexity));

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const sortVal = (p: ProgramStats) => (sort === "criticality" ? p.criticality_score : p[sort]);
    return programs
      .filter((p) => !level || p.criticality === level)
      .filter((p) => !needle || `${p.program} ${p.file} ${p.purpose} ${p.type}`.toLowerCase().includes(needle))
      .sort((a, b) => sortVal(b) - sortVal(a));
  }, [programs, q, level, sort]);

  return (
    <div className="card" data-tour="critical">
      <div className="card-head">
        <h3><LuFileCode2 size={17} className="h-ico" /> Program details</h3>
        <span className="subtle">{rows.length} of {programs.length} programs · click a row to open the code</span>
      </div>
      <div className="prog-tools">
        <label className="prog-search">
          <LuSearch size={15} />
          <input placeholder="Search programs, files or purpose" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <div className="row-wrap" style={{ gap: 6 }}>
          <button className={`chip ${level === "" ? "on" : ""}`} onClick={() => setLevel("")}>All</button>
          {LEVELS.map((l) => {
            const c = programs.filter((p) => p.criticality === l).length;
            return c ? (
              <button key={l} className={`chip ${level === l ? "on" : ""}`} onClick={() => setLevel(level === l ? "" : l)}>
                <i className="dot" style={{ background: LEVEL_COLOR[l] }} />{l} · {c}
              </button>
            ) : null;
          })}
        </div>
        <div className="seg" style={{ marginLeft: "auto" }}>
          {SORTS.map((s) => <button key={s.key} className={sort === s.key ? "on" : ""} onClick={() => setSort(s.key)}>{s.label}</button>)}
        </div>
      </div>
      <div className="table-wrap" style={{ maxHeight: 460 }}>
        <table className="data">
          <thead><tr><th>Program</th><th>What it does</th><th style={{ textAlign: "right" }}>Lines</th><th>Complexity</th><th style={{ textAlign: "right" }}>Callers / calls</th><th style={{ textAlign: "right" }}>Rules</th><th>Risk</th></tr></thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={7} className="subtle">No programs match.</td></tr>}
            {rows.map((p) => (
              <tr key={p.program} className="clickable" onClick={() => onOpen(p.file, 1)}>
                <td><b className="mono" style={{ fontSize: 13 }}>{p.program}</b><div className="subtle">{p.type}</div></td>
                <td className="subtle" style={{ fontSize: 13, maxWidth: 360 }}>{p.criticality_reasons.length ? p.criticality_reasons.join("; ") : p.purpose}</td>
                <td className="num">{fmtNumber(p.loc)}</td>
                <td><div className="cc"><span className="track"><span style={{ width: `${(p.complexity / maxCc) * 100}%` }} /></span><b>{p.complexity}</b></div></td>
                <td className="num">{p.fan_in} / {p.fan_out}</td>
                <td className="num">{p.business_rules}</td>
                <td><span className={`tag ${criticalityTag(p.criticality)}`}>{p.criticality}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
