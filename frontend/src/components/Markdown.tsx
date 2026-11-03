import DOMPurify from "dompurify";
import { marked } from "marked";
import { useMemo } from "react";

const SOURCE_REF = /<code>([\w./-]+\.[A-Za-z0-9]+):(\d+)<\/code>/g;

/** Render generated Markdown; `file:line` references become clickable source links. */
export function Markdown({ text, onSource }: { text: string; onSource?: (file: string, line: number) => void }) {
  const html = useMemo(() => {
    const raw = marked.parse(text, { async: false, gfm: true }) as string;
    const linked = raw.replace(SOURCE_REF, (_m, file: string, line: string) =>
      `<a href="#" class="src" data-file="${file}" data-line="${line}">${file}:${line}</a>`);
    return DOMPurify.sanitize(linked, { ADD_ATTR: ["data-file", "data-line"] });
  }, [text]);

  return (
    <div
      className="doc"
      onClick={(e) => {
        const a = (e.target as HTMLElement).closest("a.src") as HTMLAnchorElement | null;
        if (a && onSource) {
          e.preventDefault();
          onSource(a.dataset.file!, Number(a.dataset.line));
        }
      }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
