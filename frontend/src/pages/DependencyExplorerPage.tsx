import { useState } from "react";
import { api, SearchResult } from "../api";
import { Link } from "react-router-dom";

export function DependencyExplorerPage() {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function search() {
    if (!query.trim()) return;
    setLoading(true);
    setError("");
    try {
      const r = await api.search(query, type || undefined);
      setResults(r.results);
    } catch (e: any) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section>
      <h2>Dependency Explorer</h2>
      <div style={{ display: "flex", gap: 8 }}>
        <input
          placeholder="Search apps, QVDs, tables, tasks"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && search()}
          style={{ flex: 1, padding: 8 }}
        />
        <select value={type} onChange={(e) => setType(e.target.value)} style={{ padding: 8 }}>
          <option value="">All types</option>
          <option value="App">App</option>
          <option value="QVD">QVD</option>
          <option value="Table">Table</option>
          <option value="Task">Task</option>
        </select>
        <button onClick={search} disabled={loading}>
          {loading ? "Searching…" : "Search"}
        </button>
      </div>
      {error && <p style={{ color: "crimson" }}>{error}</p>}
      <table style={{ width: "100%", marginTop: 16, borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ background: "#f1f5f9" }}>
            <th style={th}>Type</th>
            <th style={th}>ID</th>
            <th style={th}>Name</th>
            <th style={th}>Open</th>
          </tr>
        </thead>
        <tbody>
          {results.map((r) => (
            <tr key={`${r.type}::${r.id}`}>
              <td style={td}>{r.type}</td>
              <td style={td}>{r.id}</td>
              <td style={td}>{r.name || ""}</td>
              <td style={td}>
                {r.type === "App" ? (
                  <Link to={`/app/${encodeURIComponent(r.id)}`}>Details</Link>
                ) : (
                  <Link to={`/impact?type=${r.type}&id=${encodeURIComponent(r.id)}`}>Impact</Link>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

const th: React.CSSProperties = { textAlign: "left", padding: 8, borderBottom: "1px solid #e2e8f0" };
const td: React.CSSProperties = { padding: 8, borderBottom: "1px solid #f1f5f9" };
