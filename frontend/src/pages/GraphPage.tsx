import { useCallback, useEffect, useMemo, useState } from "react";
import { api, AppStatus, GraphEdgeView, GraphNode } from "../api";
import { LineageGraph } from "../components/LineageGraph";
import { SearchableSelect, ComboOption } from "../components/SearchableSelect";
import { AppStatusBadge, STATUS_META, StatusCounts, isNoise } from "../components/AppStatus";
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
  const [refreshing, setRefreshing] = useState(false);
  const [notice, setNotice] = useState("");

  const loadApps = useCallback(
    () => api.listApps().then((r) => setApps(r.apps)).catch((e) => setError(String(e))),
    [],
  );
  useEffect(() => {
    loadApps();
  }, [loadApps]);

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

  // The status is part of the hint, so typing "dev copy" or "live" filters by it too.
  const options: ComboOption[] = useMemo(
    () =>
      apps.map((a) => {
        const meta = a.app_status ? STATUS_META[a.app_status as keyof typeof STATUS_META] : null;
        return {
          value: a.app_id,
          label: a.name || a.app_id,
          hint: meta ? `${meta.label} · ${a.app_id}` : a.app_id,
        };
      }),
    [apps],
  );

  const appStatusCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const a of apps) if (a.app_status) c[a.app_status] = (c[a.app_status] ?? 0) + 1;
    return c;
  }, [apps]);

  const selectedApp = selected?.type === "App" ? apps.find((a) => a.app_id === selected.id) : null;
  const originalApp = selectedApp?.original_app_id
    ? apps.find((a) => a.app_id === selectedApp.original_app_id)
    : null;

  async function refreshStatus() {
    setRefreshing(true);
    setNotice("");
    try {
      const r = await api.refreshAppStatus();
      setNotice(
        `Re-classified ${r.apps_classified} apps (stale after ${r.stale_days} days): ` +
          Object.entries(r.counts)
            .map(([s, n]) => `${n} ${STATUS_META[s as keyof typeof STATUS_META]?.label ?? s}`)
            .join(", "),
      );
      await loadApps();
    } catch (e) {
      setError(String(e));
    } finally {
      setRefreshing(false);
    }
  }

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
        <button
          onClick={refreshStatus}
          disabled={refreshing}
          title="Re-check every app's publish state, reload time and reload tasks in QRS. Takes seconds; no scripts are re-read."
        >
          {refreshing ? "Refreshing status…" : "Refresh app status"}
        </button>
      </div>

      {apps.length > 0 && Object.keys(appStatusCounts).length > 0 && (
        <p style={{ fontSize: 12, color: "#64748b", margin: "0 0 10px" }}>
          {apps.length.toLocaleString()} apps: <StatusCounts counts={appStatusCounts} />
        </p>
      )}
      {notice && <p style={{ fontSize: 12, color: "#166534", margin: "0 0 10px" }}>{notice}</p>}

      {selectedApp && isNoise(selectedApp.app_status) && (
        <div
          style={{
            border: `1px solid ${STATUS_META[selectedApp.app_status as AppStatus].border}`,
            background: STATUS_META[selectedApp.app_status as AppStatus].bg,
            borderRadius: 8,
            padding: "8px 12px",
            marginBottom: 10,
            fontSize: 13,
            display: "flex",
            gap: 10,
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          <AppStatusBadge status={selectedApp.app_status} />
          <span>{selectedApp.status_reason}</span>
          {originalApp && (
            <button
              onClick={() => setSelected({ type: "App", id: originalApp.app_id })}
              style={{ marginLeft: "auto" }}
            >
              View original: {originalApp.name}
            </button>
          )}
        </div>
      )}

      {selected && !loading && counts && (
        <p style={{ color: "#475569", fontSize: 13, margin: "0 0 10px" }}>
          <strong>{selectedLabel}</strong>{" "}
          {selectedApp && <AppStatusBadge status={selectedApp.app_status} reason={selectedApp.status_reason} />} —{" "}
          {counts.upstream} upstream ·{" "}
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
