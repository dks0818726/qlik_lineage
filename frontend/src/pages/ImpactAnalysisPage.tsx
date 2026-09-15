import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ImpactCandidate, ImpactChainNode, ImpactReport, SearchResult } from "../api";

const TYPES = ["App", "QVD", "Table", "Connection", "Task"];

const TYPE_COLORS: Record<string, string> = {
  App: "#2563eb",
  QVD: "#16a34a",
  Table: "#f59e0b",
  Connection: "#9333ea",
  Task: "#dc2626",
  Owner: "#0891b2",
  Stream: "#475569",
};

const PLACEHOLDERS: Record<string, string> = {
  App: "Type an app name, e.g. ATH - Public Lands",
  QVD: "Type a QVD file name, e.g. e_footweardeck.qvd",
  Table: "Type a table name, e.g. mstry_date_dim",
  Connection: "Type a connection name, e.g. DDWP",
  Task: "Type a task name, e.g. EBIR_Sales_Analysis",
};

export function ImpactAnalysisPage() {
  const [params] = useSearchParams();
  const [type, setType] = useState(params.get("type") || "Table");
  const [query, setQuery] = useState(params.get("id") || "");
  const [depth, setDepth] = useState(5);
  const [report, setReport] = useState<ImpactReport | null>(null);
  const [suggestions, setSuggestions] = useState<SearchResult[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const boxRef = useRef<HTMLDivElement | null>(null);

  async function run(ref: string = query) {
    if (!ref.trim()) {
      setError("Enter a name or id to analyze.");
      return;
    }
    setError("");
    setLoading(true);
    setShowSuggestions(false);
    try {
      setReport(await api.impactReport(type, ref, depth));
    } catch (e: any) {
      setError(String(e));
      setReport(null);
    } finally {
      setLoading(false);
    }
  }

  // Live suggestions as the user types: the exact graph id is usually unguessable
  // (app GUIDs, full lib:// paths), so the id gets picked from search rather than typed.
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setSuggestions([]);
      return;
    }
    const timer = setTimeout(() => {
      api
        .search(q, type)
        .then((r) => setSuggestions(r.results.slice(0, 8)))
        .catch(() => setSuggestions([]));
    }, 250);
    return () => clearTimeout(timer);
  }, [query, type]);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setShowSuggestions(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  // Deep-link support (?type=&id=), preserved from the previous version.
  useEffect(() => {
    if (params.get("id")) run(params.get("id")!);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function pick(c: ImpactCandidate | SearchResult) {
    setQuery(c.id);
    setShowSuggestions(false);
    run(c.id);
  }

  const impactedTypes = useMemo(() => Object.keys(report?.impacted || {}).sort(), [report]);

  return (
    <section>
      <h2>Impact Analysis</h2>
      <p style={{ color: "#64748b", marginTop: 0 }}>
        See everything that breaks if an object changes. Search by name — you no longer
        need the exact internal id.
      </p>

      <div style={{ display: "flex", gap: 8, marginBottom: 12, alignItems: "flex-start" }}>
        <select
          value={type}
          onChange={(e) => {
            setType(e.target.value);
            setReport(null);
          }}
          style={{ padding: 8, borderRadius: 6, border: "1px solid #cbd5e1" }}
        >
          {TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>

        <div ref={boxRef} style={{ position: "relative", flex: 1 }}>
          <input
            placeholder={PLACEHOLDERS[type] || "Search by name or id"}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setShowSuggestions(true);
            }}
            onFocus={() => setShowSuggestions(true)}
            onKeyDown={(e) => e.key === "Enter" && run()}
            style={{
              width: "100%",
              padding: 8,
              borderRadius: 6,
              border: "1px solid #cbd5e1",
              boxSizing: "border-box",
            }}
          />
          {showSuggestions && suggestions.length > 0 && (
            <ul style={dropdown}>
              {suggestions.map((s) => (
                <li
                  key={`${s.type}:${s.id}`}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    pick(s);
                  }}
                  style={dropdownItem}
                >
                  <div style={{ fontSize: 13, color: "#0f172a" }}>{s.name || s.id}</div>
                  <div style={{ fontSize: 11, color: "#94a3b8", fontFamily: "monospace" }}>
                    {s.id}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <label style={{ fontSize: 12, color: "#475569", alignSelf: "center" }}>
          Depth{" "}
          <input
            type="number"
            min={1}
            max={20}
            value={depth}
            onChange={(e) => setDepth(Number(e.target.value))}
            style={{ width: 60, padding: 8, borderRadius: 6, border: "1px solid #cbd5e1" }}
          />
        </label>
        <button onClick={() => run()} disabled={loading} style={{ padding: "8px 16px" }}>
          {loading ? "Analyzing…" : "Analyze"}
        </button>
      </div>

      {error && <p style={{ color: "crimson" }}>{error}</p>}
      {loading && <p style={{ color: "#64748b" }}>Tracing dependencies…</p>}

      {/* Ambiguous or unknown: show what the user could have meant, instead of a blank page. */}
      {!loading && report && report.status !== "ok" && (
        <div style={{ border: "1px solid #fcd34d", background: "#fffbeb", borderRadius: 8, padding: 14 }}>
          <strong style={{ color: "#92400e" }}>{report.message}</strong>
          {report.candidates && report.candidates.length > 0 && (
            <ul style={{ listStyle: "none", padding: 0, margin: "10px 0 0" }}>
              {report.candidates.map((c) => (
                <li key={c.id} style={{ marginBottom: 4 }}>
                  <button onClick={() => pick(c)} style={candidateBtn}>
                    <span style={{ fontSize: 13 }}>{c.name || c.id}</span>
                    <span
                      style={{
                        fontSize: 11,
                        color: "#94a3b8",
                        fontFamily: "monospace",
                        marginLeft: 8,
                      }}
                    >
                      {c.id}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {report.status === "not_found" && (
            <p style={{ margin: "8px 0 0", fontSize: 13, color: "#92400e" }}>
              Try a shorter keyword, or switch the type dropdown — the object may be
              registered under a different type.
            </p>
          )}
        </div>
      )}

      {!loading && report?.status === "ok" && report.summary && (
        <>
          <div style={summaryBar}>
            <div>
              <div style={{ fontSize: 12, color: "#64748b" }}>Analyzing {report.node!.type}</div>
              <div style={{ fontSize: 17, fontWeight: 700, color: "#0f172a" }}>
                {report.node!.name}
              </div>
              <div style={{ fontSize: 11, color: "#94a3b8", fontFamily: "monospace" }}>
                {report.node!.id}
              </div>
            </div>
            <div style={{ display: "flex", gap: 22, marginLeft: "auto", textAlign: "center" }}>
              <Stat label="Impacted objects" value={report.summary.total_impacted} accent="#dc2626" />
              <Stat label="Upstream paths" value={report.summary.upstream_paths} accent="#0891b2" />
              <Stat label="Downstream paths" value={report.summary.downstream_paths} accent="#2563eb" />
            </div>
          </div>

          {report.summary.total_impacted === 0 ? (
            <div
              style={{
                border: "1px solid #e2e8f0",
                borderRadius: 8,
                padding: 16,
                background: "#fff",
                color: "#64748b",
              }}
            >
              Nothing downstream depends on this {report.node!.type.toLowerCase()} — changing
              it looks safe. If you expected dependents, try a higher depth or re-run a scan.
            </div>
          ) : (
            <>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
                {Object.entries(report.summary.by_type).map(([t, n]) => (
                  <span key={t} style={{ ...pill, background: TYPE_COLORS[t] || "#64748b" }}>
                    {n} {t}
                    {n === 1 ? "" : "s"}
                  </span>
                ))}
                {report.summary.truncated && (
                  <span style={{ ...pill, background: "#94a3b8" }}>showing first 200</span>
                )}
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                  gap: 12,
                }}
              >
                {impactedTypes.map((t) => (
                  <div key={t} style={card}>
                    <h3 style={{ margin: "0 0 8px", fontSize: 14, color: TYPE_COLORS[t] || "#334155" }}>
                      {t} ({report.impacted![t].length})
                    </h3>
                    <ul
                      style={{
                        paddingLeft: 16,
                        margin: 0,
                        fontSize: 12,
                        maxHeight: 240,
                        overflowY: "auto",
                      }}
                    >
                      {report.impacted![t].map((item) => (
                        <li key={item.id} title={item.id} style={{ marginBottom: 3 }}>
                          {item.name}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </>
          )}

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 12 }}>
            <ChainPanel title="Upstream (what feeds it)" chains={report.upstream || []} />
            <ChainPanel title="Downstream (what it feeds)" chains={report.downstream || []} />
          </div>
        </>
      )}

      {!loading && !report && !error && (
        <p style={{ color: "#94a3b8" }}>
          Choose a type and search for an object to see its impact.
        </p>
      )}
    </section>
  );
}

function Stat({ label, value, accent }: { label: string; value: number; accent: string }) {
  return (
    <div>
      <div style={{ fontSize: 22, fontWeight: 700, color: accent }}>{value}</div>
      <div style={{ fontSize: 11, color: "#64748b" }}>{label}</div>
    </div>
  );
}

function ChainPanel({
  title,
  chains,
}: {
  title: string;
  chains: { depth: number; chain: ImpactChainNode[] }[];
}) {
  return (
    <div style={card}>
      <h3 style={{ marginTop: 0, fontSize: 14 }}>
        {title} ({chains.length})
      </h3>
      <ul style={{ paddingLeft: 16, margin: 0, fontSize: 12, maxHeight: 260, overflowY: "auto" }}>
        {chains.length ? (
          chains.map((c, i) => (
            <li key={i} style={{ marginBottom: 4 }}>
              {c.chain.map((n) => n.name).join("  →  ")}
            </li>
          ))
        ) : (
          <li style={{ color: "#94a3b8" }}>(none)</li>
        )}
      </ul>
    </div>
  );
}

const card: React.CSSProperties = {
  border: "1px solid #e2e8f0",
  borderRadius: 8,
  padding: 12,
  background: "#fff",
};

const summaryBar: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 16,
  border: "1px solid #e2e8f0",
  borderRadius: 8,
  padding: 14,
  background: "#fff",
  marginBottom: 12,
};

const pill: React.CSSProperties = {
  color: "#fff",
  borderRadius: 999,
  padding: "3px 11px",
  fontSize: 12,
  fontWeight: 600,
};

const dropdown: React.CSSProperties = {
  position: "absolute",
  zIndex: 30,
  top: "calc(100% + 4px)",
  left: 0,
  right: 0,
  maxHeight: 300,
  overflowY: "auto",
  margin: 0,
  padding: 4,
  listStyle: "none",
  background: "#fff",
  border: "1px solid #cbd5e1",
  borderRadius: 6,
  boxShadow: "0 8px 20px rgba(15,23,42,0.12)",
};

const dropdownItem: React.CSSProperties = {
  padding: "6px 10px",
  cursor: "pointer",
  borderRadius: 4,
};

const candidateBtn: React.CSSProperties = {
  background: "#fff",
  border: "1px solid #e2e8f0",
  borderRadius: 6,
  padding: "6px 10px",
  cursor: "pointer",
  textAlign: "left",
  width: "100%",
};
