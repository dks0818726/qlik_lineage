import { useEffect, useMemo, useRef, useState } from "react";

export interface ComboOption {
  value: string;
  label: string;
  /** Secondary line shown under the label, e.g. the raw id. */
  hint?: string;
}

/** A single control that is both a text filter and a dropdown.
 *
 * A plain <select> is unusable once there are hundreds of apps, and a bare
 * <datalist> gives no control over what is displayed versus what is submitted
 * (the value must equal the visible text). This keeps the label on screen while
 * submitting the underlying id.
 */
export function SearchableSelect({
  options,
  value,
  onChange,
  placeholder = "Search…",
  minWidth = 380,
  maxVisible = 50,
}: {
  options: ComboOption[];
  value: string | null;
  onChange: (value: string) => void;
  placeholder?: string;
  minWidth?: number;
  maxVisible?: number;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const boxRef = useRef<HTMLDivElement | null>(null);

  const selectedLabel = useMemo(
    () => options.find((o) => o.value === value)?.label ?? "",
    [options, value],
  );

  // `matched` is every option that matches; `filtered` is the capped slice we
  // actually render. Keeping both lets the footer report how many were hidden,
  // which matters when the full option list is in the thousands.
  const matched = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (o) =>
        o.label.toLowerCase().includes(q) ||
        (o.hint ?? "").toLowerCase().includes(q),
    );
  }, [options, query]);

  const filtered = useMemo(() => matched.slice(0, maxVisible), [matched, maxVisible]);

  // Clicking anywhere else closes the list and restores the selected label.
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  function commit(option: ComboOption) {
    onChange(option.value);
    setQuery("");
    setOpen(false);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setHighlight((h) => Math.min(h + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      if (open && filtered[highlight]) {
        e.preventDefault();
        commit(filtered[highlight]);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
      setQuery("");
    }
  }

  return (
    <div ref={boxRef} style={{ position: "relative", minWidth }}>
      <input
        value={open ? query : selectedLabel}
        placeholder={selectedLabel || placeholder}
        onChange={(e) => {
          setQuery(e.target.value);
          setHighlight(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        style={{
          width: "100%",
          padding: "8px 30px 8px 10px",
          border: "1px solid #cbd5e1",
          borderRadius: 6,
          fontSize: 14,
          boxSizing: "border-box",
        }}
      />
      <span
        onClick={() => setOpen((o) => !o)}
        style={{
          position: "absolute",
          right: 10,
          top: 10,
          cursor: "pointer",
          color: "#64748b",
          fontSize: 12,
          userSelect: "none",
        }}
      >
        ▼
      </span>
      {open && (
        <ul
          style={{
            position: "absolute",
            zIndex: 30,
            top: "calc(100% + 4px)",
            left: 0,
            right: 0,
            maxHeight: 320,
            overflowY: "auto",
            margin: 0,
            padding: 4,
            listStyle: "none",
            background: "#fff",
            border: "1px solid #cbd5e1",
            borderRadius: 6,
            boxShadow: "0 8px 20px rgba(15,23,42,0.12)",
          }}
        >
          {filtered.length === 0 && (
            <li style={{ padding: 10, color: "#94a3b8", fontSize: 13 }}>No matches</li>
          )}
          {filtered.map((o, i) => (
            <li
              key={o.value}
              onMouseEnter={() => setHighlight(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                commit(o);
              }}
              style={{
                padding: "7px 10px",
                cursor: "pointer",
                borderRadius: 4,
                background:
                  i === highlight ? "#eff6ff" : o.value === value ? "#f1f5f9" : "transparent",
              }}
            >
              <div style={{ fontSize: 14, color: "#0f172a" }}>{o.label}</div>
              {o.hint && (
                <div style={{ fontSize: 11, color: "#94a3b8", fontFamily: "monospace" }}>
                  {o.hint}
                </div>
              )}
            </li>
          ))}
          {matched.length > filtered.length && (
            <li style={{ padding: "6px 10px", color: "#94a3b8", fontSize: 11 }}>
              Showing {filtered.length} of {matched.length} matches — keep typing to narrow
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
