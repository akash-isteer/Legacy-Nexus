import { useEffect, useState } from "react";
import { LuDownload, LuFileArchive } from "react-icons/lu";
import { AppShell } from "../components/AppShell";
import { Markdown } from "../components/Markdown";
import { useProject } from "../components/ProjectContext";
import { ErrorBox, Loading, useToast } from "../components/ui";
import { download } from "../lib/api";
import { useApi } from "../lib/hooks";

type DocMeta = { id: string; title: string; category: string };

export function DocsPage() {
  const { project, openSource } = useProject();
  const pid = project!.id;
  const toast = useToast();
  const { data: list, error } = useApi<DocMeta[]>(`/projects/${pid}/docs`);
  const [active, setActive] = useState<string | null>(null);
  const { data: doc, loading } = useApi<DocMeta & { markdown: string }>(active ? `/projects/${pid}/docs/${active}` : null);

  useEffect(() => {
    if (list && !active) setActive(list[0]?.id ?? null);
  }, [list, active]);

  const saveMd = () => {
    if (!doc) return;
    const blob = new Blob([doc.markdown], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${doc.id}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <AppShell title="Documentation" actions={
      <button className="btn btn-sm" onClick={() => download(`/projects/${pid}/docs/export`, "documentation.zip").catch((e) => toast(e.message, true))}>
        <LuFileArchive size={14} /> Download all (.zip)
      </button>
    }>
      {error && <ErrorBox message={error} />}
      <div className="docs-layout">
        <nav className="doc-list" aria-label="Documents">
          {list?.map((d) => (
            <button key={d.id} className={active === d.id ? "on" : ""} onClick={() => setActive(d.id)}>
              {d.title}
              <div className="subtle">{d.category}</div>
            </button>
          ))}
        </nav>
        <div className="card card-pad" style={{ minHeight: 400 }}>
          {loading && <Loading />}
          {doc && !loading && (
            <>
              <div className="row" style={{ justifyContent: "flex-end", marginBottom: -6 }}>
                <button className="btn btn-sm" onClick={saveMd}><LuDownload size={14} /> Markdown</button>
              </div>
              <Markdown text={doc.markdown} onSource={(f, l) => openSource(f, l)} />
            </>
          )}
        </div>
      </div>
    </AppShell>
  );
}
