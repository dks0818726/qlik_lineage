import { useEffect, useState } from "react";
import { api, GraphEdgeView, GraphNode } from "../api";
import { LineageGraph } from "../components/LineageGraph";
import { useNavigate } from "react-router-dom";

export function GraphPage() {
  const navigate = useNavigate();
  const [apps, setApps] = useState<any[]>([]);
  const [selected, setSelected] = useState<{ type: string; id: string } | null>(null);
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdgeView[]>([]);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    api.listApps().then((r) => setApps(r.apps)).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!selected) return;
    api
      .neighborhood(selected.type, selected.id, 2)
      .then((r) => {
        setNodes(r.nodes);
        setEdges(r.edges);
      })
      .catch((e) => setError(String(e)));
  }, [selected]);

  return (
    <section>
      <h2>Graph Visualization</h2>
      <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
        <select
          onChange={(e) => setSelected({ type: "App", id: e.target.value })}
          defaultValue=""
          style={{ padding: 8, minWidth: 320 }}
        >
          <option value="" disabled>
            Select an app…
          </option>
          {apps.map((a) => (
            <option key={a.app_id} value={a.app_id}>
              {a.name} ({a.app_id})
            </option>
          ))}
        </select>
        <button onClick={() => api.scan("delta")}>Trigger delta scan</button>
        <button onClick={() => api.scan("full")}>Trigger full scan</button>
      </div>
      {error && <p style={{ color: "crimson" }}>{error}</p>}
      {nodes.length > 0 ? (
        <LineageGraph
          nodes={nodes}
          edges={edges}
          onNodeClick={(n) => {
            if (n.type === "App") navigate(`/app/${encodeURIComponent(n.id)}`);
            else setSelected(n);
          }}
        />
      ) : (
        <p style={{ color: "#64748b" }}>Select an app to render the lineage neighborhood.</p>
      )}
    </section>
  );
}
