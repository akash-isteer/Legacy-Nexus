import { useState } from "react";
import { LuChevronDown, LuChevronRight, LuHistory } from "react-icons/lu";
import type { DataHotspot, RunChanges, SensitiveData } from "../lib/types";

type Open = (file: string, line: number) => void;

const list = (xs: string[], max = 4) => xs.length <= max ? xs.join(", ") : `${xs.slice(0, max).join(", ")} +${xs.length - max} more`;
const when = (iso?: string | null) => iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "the last run";

/** One line on what is different from the previous analysis. */
export function ChangesStrip({ changes }: { changes: RunChanges }) {
  const [open, setOpen] = useState(false);
  if (changes.baseline)
    return <div className="changes"><LuHistory size={16} /><span className="subtle">First analysis of this application. Changes will show here after the next run.</span></div>;
  if (changes.unchanged)
    return <div className="changes"><LuHistory size={16} /><span>No changes since {when(changes.compared_to)}.</span></div>;

  const f = changes.files ?? { added: [], removed: [], changed: [] };
  const r = changes.rules ?? { added: 0, removed: 0, added_sample: [], removed_sample: [] };
  const parts: string[] = [];
  if (f.changed.length) parts.push(`${f.changed.length} file(s) edited`);
  if (f.added.length) parts.push(`${f.added.length} added`);
  if (f.removed.length) parts.push(`${f.removed.length} removed`);
  for (const c of changes.components ?? []) {
    const bits = [c.added.length && `+${c.added.length}`, c.removed.length && `−${c.removed.length}`].filter(Boolean).join(" ");
    parts.push(`${bits} ${c.label}`);
  }
  if (r.added || r.removed) parts.push(`business rules ${[r.added && `+${r.added}`, r.removed && `−${r.removed}`].filter(Boolean).join(" ")}`);

  return (
    <div className="changes-wrap">
      <button className="changes clickable" onClick={() => setOpen(!open)} aria-expanded={open}>
        <LuHistory size={16} />
        <span><b>Since {when(changes.compared_to)}:</b> {parts.join(" · ")}</span>
        {open ? <LuChevronDown size={15} /> : <LuChevronRight size={15} />}
      </button>
      {open && (
        <div className="changes-detail">
          {f.changed.length > 0 && <p><b>Edited:</b> <span className="mono">{list(f.changed, 10)}</span></p>}
          {f.added.length > 0 && <p><b>New files:</b> <span className="mono">{list(f.added, 10)}</span></p>}
          {f.removed.length > 0 && <p><b>Removed files:</b> <span className="mono">{list(f.removed, 10)}</span></p>}
          {(changes.components ?? []).map((c) => (
            <p key={c.kind}><b>{c.label[0].toUpperCase() + c.label.slice(1)}:</b>{" "}
              {c.added.length > 0 && <>new <span className="mono">{list(c.added, 8)}</span></>}
              {c.added.length > 0 && c.removed.length > 0 && "; "}
              {c.removed.length > 0 && <>gone <span className="mono">{list(c.removed, 8)}</span></>}
            </p>
          ))}
          {r.added > 0 && <p><b>New business rules:</b> {r.added_sample.join("; ")}{r.added > r.added_sample.length && ` and ${r.added - r.added_sample.length} more`}</p>}
          {r.removed > 0 && <p><b>Rules no longer found:</b> {r.removed_sample.join("; ")}{r.removed > r.removed_sample.length && ` and ${r.removed - r.removed_sample.length} more`}</p>}
        </div>
      )}
    </div>
  );
}

const KIND_LABEL = { table: "table", copybook: "copybook", program: "program" } as const;

/** Personal and financial fields, grouped by kind, with where they live and who uses them. */
export function SensitiveDataCard({ data, onOpen }: { data: SensitiveData; onOpen: Open }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="card" data-tour="sensitive">
      <div className="card-head"><h3>Sensitive data</h3><span className="subtle">Personal and financial fields, found by name</span></div>
      <div className="card-body" style={{ paddingBottom: 6 }}>
        {data.fields === 0
          ? <p className="subtle">No personal or financial fields were recognised by name.</p>
          : <p className="subtle" style={{ margin: 0 }}>
              {data.fields} field(s) in {data.tables.length} table(s), used by {data.programs.length} program(s).
              Check masking and access rules for these before moving data or code.
            </p>}
      </div>
      {data.fields > 0 && (
        <div className="table-wrap" style={{ maxHeight: 340 }}>
          <table className="data">
            <tbody>
              {data.categories.map((c) => {
                const where = [...new Set(c.fields.filter((f) => f.kind === "table").map((f) => f.where))];
                const users = [...new Set(c.fields.flatMap((f) => f.used_by))].sort();
                const isOpen = open === c.key;
                return [
                  <tr key={c.key} className="clickable" onClick={() => setOpen(isOpen ? null : c.key)}>
                    <td style={{ width: 22 }}>{isOpen ? <LuChevronDown size={14} /> : <LuChevronRight size={14} />}</td>
                    <td><b>{c.label}</b><div className="subtle">{c.count} field(s)</div></td>
                    <td className="subtle" style={{ fontSize: 13 }}>
                      {where.length > 0 && <div>Stored in <span className="mono">{list(where)}</span></div>}
                      {users.length > 0 && <div>Used by <span className="mono">{list(users)}</span></div>}
                    </td>
                  </tr>,
                  ...(isOpen ? c.fields.map((f) => (
                    <tr key={`${c.key}:${f.kind}:${f.where}.${f.field}`} className={`sub-row ${f.file ? "clickable" : ""}`}
                        onClick={() => f.file && onOpen(f.file, f.line ?? 1)}>
                      <td />
                      <td className="mono" style={{ fontSize: 12.5 }}>{f.field}</td>
                      <td className="subtle" style={{ fontSize: 12.5 }}>{KIND_LABEL[f.kind]} <span className="mono">{f.where}</span>{f.used_by.length > 0 && ` · ${list(f.used_by, 3)}`}</td>
                    </tr>
                  )) : []),
                ];
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Tables ranked by how many programs update them. */
export function DataHotspotsCard({ rows, onOpen }: { rows: DataHotspot[]; onOpen: Open }) {
  const shared = rows.filter((r) => r.shared_write).length;
  return (
    <div className="card" data-tour="hotspots">
      <div className="card-head"><h3>Shared data</h3><span className="subtle">Who updates and reads each table</span></div>
      <div className="card-body" style={{ paddingBottom: 6 }}>
        <p className="subtle" style={{ margin: 0 }}>
          {shared > 0
            ? `${shared} table(s) are updated by more than one program. Agree who owns each one before changing any of them.`
            : "Every table is updated by at most one program, so data ownership is clear."}
        </p>
      </div>
      <div className="table-wrap" style={{ maxHeight: 340 }}>
        <table className="data">
          <thead><tr><th>Table</th><th>Updated by</th><th>Read by</th></tr></thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={3} className="subtle">No database tables were found.</td></tr>}
            {rows.map((r) => (
              <tr key={r.table} className={r.file ? "clickable" : ""} onClick={() => r.file && onOpen(r.file, r.line ?? 1)}>
                <td>
                  <b className="mono" style={{ fontSize: 13 }}>{r.table}</b>
                  {r.shared_write && <div><span className="tag tag-amber">Shared updates</span></div>}
                </td>
                <td className="mono" style={{ fontSize: 12.5 }}>{r.writers.length ? list(r.writers) : <span className="subtle">none</span>}</td>
                <td className="mono" style={{ fontSize: 12.5 }}>{r.readers.length ? list(r.readers) : <span className="subtle">none</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
