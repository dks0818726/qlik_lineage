import { useEffect, useRef, useState } from "react";
import { Network } from "vis-network/standalone";
import { DataSet } from "vis-data/standalone";
import type { GraphEdgeView, GraphNode } from "../api";

const COLORS: Record<string, string> = {
  App: "#2563eb",
  QVD: "#16a34a",
  Table: "#f59e0b",
  Connection: "#9333ea",
  Task: "#dc2626",
  Owner: "#0891b2",
  Stream: "#475569",
  Schedule: "#a16207",
};

export function LineageGraph({
  nodes,
  edges,
  height = 520,
  onNodeClick,
}: {
  nodes: GraphNode[];
  edges: GraphEdgeView[];
  height?: number;
  onNodeClick?: (node: GraphNode) => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [, setReady] = useState(false);

  useEffect(() => {
    if (!ref.current) return;
    const nodeData = new DataSet(
      nodes.map((n) => ({
        id: `${n.type}::${n.id}`,
        label: `${n.id}\n(${n.type})`,
        color: { background: COLORS[n.type] || "#64748b", border: "#1e293b" },
        font: { color: "#fff", size: 12 },
        shape: "box",
      })),
    );
    const edgeData = new DataSet(
      edges.map((e, i) => ({
        id: i,
        from: `${e.source_type}::${e.source}`,
        to: `${e.target_type}::${e.target}`,
        label: e.relation,
        arrows: "to",
        font: { size: 10, align: "middle" },
        color: { color: "#94a3b8" },
      })),
    );
    const network = new Network(
      ref.current,
      { nodes: nodeData, edges: edgeData },
      {
        physics: { solver: "forceAtlas2Based", stabilization: { iterations: 250 } },
        layout: { improvedLayout: true },
        interaction: { hover: true, tooltipDelay: 200 },
        edges: { smooth: { enabled: true, type: "dynamic", roundness: 0.4 } },
      },
    );
    if (onNodeClick) {
      network.on("click", (params) => {
        const nodeId = params.nodes?.[0];
        if (!nodeId) return;
        const [type, ...rest] = String(nodeId).split("::");
        onNodeClick({ type, id: rest.join("::") });
      });
    }
    setReady(true);
    return () => network.destroy();
  }, [nodes, edges, onNodeClick]);

  return <div ref={ref} style={{ width: "100%", height, border: "1px solid #e2e8f0", borderRadius: 8 }} />;
}
