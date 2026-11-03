import { useEffect, useState } from "react";
import { LuSearch } from "react-icons/lu";
import { api, qs } from "../lib/api";
import { TYPE_LABELS } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import type { SymbolHit } from "../lib/types";
import { TypeDot } from "./ui";

/** Autocomplete over the project's symbol index (programs, tables, columns, fields, jobs…). */
export function SymbolSearch({ projectId, types, placeholder, value, onChange, onPick, autoFocus }: {
  projectId: number;
  types?: string;
  placeholder?: string;
  value: string;
  onChange: (v: string) => void;
  onPick: (hit: SymbolHit) => void;
  autoFocus?: boolean;
}) {
  const [hits, setHits] = useState<SymbolHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const q = useDebounced(value, 180);

  useEffect(() => {
    if (!q.trim()) {
      setHits([]);
      return;
    }
    let live = true;
    api
      .get<SymbolHit[]>(`/projects/${projectId}/symbols${qs({ q, types, limit: 12 })}`)
      .then((h) => live && (setHits(h), setActive(0)))
      .catch(() => live && setHits([]));
    return () => {
      live = false;
    };
  }, [q, types, projectId]);

  const pick = (h: SymbolHit) => {
    setOpen(false);
    onPick(h);
  };

  return (
    <div className="autocomplete" style={{ flex: 1 }}>
      <div style={{ position: "relative" }}>
        <LuSearch size={16} style={{ position: "absolute", left: 12, top: 11, color: "var(--text-3)" }} />
        <input
          className="input input-mono"
          style={{ paddingLeft: 36 }}
          value={value}
          autoFocus={autoFocus}
          placeholder={placeholder}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onChange={(e) => {
            onChange(e.target.value);
            setOpen(true);
          }}
          onKeyDown={(e) => {
            if (!open || !hits.length) return;
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((a) => Math.min(hits.length - 1, a + 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(0, a - 1));
            } else if (e.key === "Enter" && hits[active]) {
              e.preventDefault();
              pick(hits[active]);
            }
          }}
          aria-autocomplete="list"
        />
      </div>
      {open && hits.length > 0 && (
        <div className="ac-list" role="listbox">
          {hits.map((h, i) => (
            <div key={h.id + h.name} className={`ac-item ${i === active ? "on" : ""}`} onMouseDown={() => pick(h)} role="option" aria-selected={i === active}>
              <TypeDot type={h.type} />
              <span className="mono" style={{ fontSize: 13 }}>{h.program && h.type === "field" ? `${h.program}.${h.name}` : h.name}</span>
              <span className="ty">{TYPE_LABELS[h.type] ?? h.type}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
