import { useCallback, useEffect, useMemo, useState } from "react";
import { api, GraphEdgeView, GraphNode } from "../api";
import { LineageGraph } from "../components/LineageGraph";
import { SearchableSelect, ComboOption } from "../components/SearchableSelect";
import { useNavigate } from "react-router-dom";

export function GraphPage() {
  const navigate = useNavigate();
  const [apps, setApps] = useState<any[]>([]);
  const [selected, setSelected] = useState<{ type: string; id: string } | null>(null);
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdgeView[]>([]);
  const [counts, setCounts] = useState<{ upstream: number; downstream: number } | null>(null);
  const [depth, setDepth] = useState(3);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    api.listApps().then((r) => setApps(r.apps)).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setLoading(true);
    setError("");
    api
      .lineageScope(selected.type, selected.id, depth, depth)
      .then((r) => {
        setNodes(r.nodes);
        setEdges(r.edges);
        setCounts({ upstream: r.counts.upstream, downstream: r.counts.downstream });
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [selected, depth]);

  const options: ComboOption[] = useMemo(
    () =>
      apps.map((a) => ({ value: a.app_id, label: a.name || a.app_id, hint: a.app_id })),
    [apps],
  );

  // Stable identity keeps the graph from rebuilding on every parent render.
  const handleNodeClick = useCallback(
    (n: GraphNode) => {
      if (n.type === "App" && n.id !== selected?.id) setSelected({ type: "App", id: n.id });
      else if (n.type !== "App") setSelected(n);
    },
    [selected?.id],
  );

  const selectedLabel = selected
    ? nodes.find((n) => n.id === selected.id)?.name ??
      apps.find((a) => a.app_id === selected.id)?.name ??
      selected.id
    : "";

  return (
    <section>
      <h2>Graph Visualization</h2>
      <div style={{ display: "flex", gap: 12, marginBottom: 12, alignItems: "center", flexWrap: "wrap" }}>
        <SearchableSelect
          options={options}
          value={selected?.type === "App" ? selected.id : null}
          onChange={(appId) => setSelected({ type: "App", id: appId })}
          placeholder="Search or select an app…"
        />
        <label style={{ fontSize: 13, color: "#475569" }}>
          Depth{" "}
          <select
            value={depth}
            onChange={(e) => setDepth(Number(e.target.value))}
            style={{ padding: 6, borderRadius: 6, border: "1px solid #cbd5e1" }}
          >
            {[1, 2, 3, 4, 5].map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        <button onClick={() => api.scan("delta")}>Trigger delta scan</button>
        <button onClick={() => api.scan("full")}>Trigger full scan</button>
      </div>

      {selected && !loading && counts && (
        <p style={{ color: "#475569", fontSize: 13, margin: "0 0 10px" }}>
          <strong>{selectedLabel}</strong> — {counts.upstream} upstream ·{" "}
          {counts.downstream} downstream · {edges.length} relationships. Upstream sits to
          the left, downstream to the right. Click any node to re-centre on it.
        </p>
      )}

      {error && <p style={{ color: "crimson" }}>{error}</p>}

      {loading ? (
        <p style={{ color: "#64748b" }}>Loading lineage…</p>
      ) : nodes.length > 0 ? (
        <LineageGraph
          nodes={nodes}
          edges={edges}
          rootKey={selected ? `${selected.type}::${selected.id}` : undefined}
          onNodeClick={handleNodeClick}
        />
      ) : selected ? (
        <p style={{ color: "#64748b" }}>
          No upstream or downstream lineage found for this node.
        </p>
      ) : (
        <p style={{ color: "#64748b" }}>
          Select an app to render its upstream and downstream lineage.
        </p>
      )}

      {selected?.type === "App" && (
        <button
          onClick={() => navigate(`/app/${encodeURIComponent(selected.id)}`)}
          style={{ marginTop: 12 }}
        >
          Open app details
        </button>
      )}
    </section>
  );
}
