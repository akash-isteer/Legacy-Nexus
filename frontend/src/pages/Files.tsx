import { useEffect, useMemo, useState } from "react";
import { LuFileCode2, LuSearch } from "react-icons/lu";
import { AppShell } from "../components/AppShell";
import { useProject } from "../components/ProjectContext";
import { CodeView, ErrorBox, Loading } from "../components/ui";
import { qs } from "../lib/api";
import { useApi } from "../lib/hooks";
import { navigate, useSearchParam } from "../lib/router";

type FileRow = { path: string; language: string | null; size_bytes: number; line_count: number | null };

export function FilesPage() {
  const { project } = useProject();
  const pid = project!.id;
  const selected = useSearchParam("path");
  const line = Number(useSearchParam("line") || 0) || undefined;
  const [filter, setFilter] = useState("");
  const { data: files, error } = useApi<FileRow[]>(`/projects/${pid}/files`);
  const { data: content, loading } = useApi<{ lines: string[] }>(selected ? `/projects/${pid}/files/content${qs({ path: selected })}` : null);

  useEffect(() => {
    if (!selected && files?.length) navigate(`/projects/${pid}/files?path=${encodeURIComponent(files[0].path)}`, true);
  }, [files, selected, pid]);

  const shown = useMemo(() => (files ?? []).filter((f) => f.path.toLowerCase().includes(filter.toLowerCase())), [files, filter]);

  return (
    <AppShell title="Source files">
      {error && <ErrorBox message={error} />}
      <div className="docs-layout" style={{ gridTemplateColumns: "300px minmax(0,1fr)" }}>
        <div className="card" style={{ position: "sticky", top: 0 }}>
          <div style={{ padding: 10, borderBottom: "1px solid var(--line)", position: "relative" }}>
            <LuSearch size={15} style={{ position: "absolute", left: 22, top: 22, color: "var(--text-3)" }} />
            <input className="input" style={{ paddingLeft: 34, height: 34 }} placeholder="Filter files" value={filter} onChange={(e) => setFilter(e.target.value)} />
          </div>
          <div style={{ maxHeight: "calc(100vh - 220px)", overflowY: "auto", padding: 6 }}>
            {shown.map((f) => (
              <button key={f.path} className={`nav-item ${selected === f.path ? "active" : ""}`}
                style={{ color: selected === f.path ? "var(--brand-ink)" : "var(--text)", fontSize: 13 }}
                onClick={() => navigate(`/projects/${pid}/files?path=${encodeURIComponent(f.path)}`)}>
                <LuFileCode2 size={15} />
                <span className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.path}</span>
                <span className="badge" style={{ color: selected === f.path ? "var(--brand)" : "var(--text-3)" }}>{f.language ?? ""}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="card" style={{ overflow: "hidden" }}>
          <div className="card-head"><h3 className="mono" style={{ fontSize: 14 }}>{selected}</h3>{content && <span className="subtle">{content.lines.length} lines</span>}</div>
          {loading && <Loading />}
          {content && !loading && <CodeView lines={content.lines} highlightFrom={line} highlightTo={line} inline />}
        </div>
      </div>
    </AppShell>
  );
}
