import { useEffect, useMemo, useRef } from "react";
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

const key = (type: string, id: string) => `${type}::${id}`;

/** Long QVD paths and table names make boxes unreadably wide. */
function shorten(label: string, max = 34): string {
  const trimmed = label.replace(/\\/g, "/");
  const tail = trimmed.includes("/") ? trimmed.split("/").pop()! : trimmed;
  return tail.length > max ? `${tail.slice(0, max - 1)}…` : tail;
}

/** Assign each node a column: negative = upstream, 0 = root, positive = downstream.
 *
 * The force simulation is what made large graphs slow to settle and hard to read.
 * Levels let vis-network use its hierarchical layout instead, which is deterministic
 * and renders immediately, placing sources on the left and consumers on the right.
 */
function computeLevels(
  nodes: GraphNode[],
  edges: GraphEdgeView[],
  rootKey: string,
): Map<string, number> {
  const outgoing = new Map<string, string[]>();
  const incoming = new Map<string, string[]>();
  for (const e of edges) {
    const from = key(e.source_type, e.source);
    const to = key(e.target_type, e.target);
    if (!outgoing.has(from)) outgoing.set(from, []);
    if (!incoming.has(to)) incoming.set(to, []);
    outgoing.get(from)!.push(to);
    incoming.get(to)!.push(from);
  }

  const levels = new Map<string, number>([[rootKey, 0]]);
  const walk = (adjacency: Map<string, string[]>, step: number) => {
    const queue: string[] = [rootKey];
    const seen = new Set<string>([rootKey]);
    while (queue.length) {
      const current = queue.shift()!;
      for (const next of adjacency.get(current) ?? []) {
        if (seen.has(next)) continue;
        seen.add(next);
        levels.set(next, (levels.get(current) ?? 0) + step);
        queue.push(next);
      }
    }
  };
  walk(outgoing, 1); // downstream → right
  walk(incoming, -1); // upstream → left

  for (const n of nodes) {
    const k = key(n.type, n.id);
    if (!levels.has(k)) {
      levels.set(k, n.direction === "upstream" ? -1 : n.direction === "downstream" ? 1 : 0);
    }
  }
  return levels;
}

export function LineageGraph({
  nodes,
  edges,
  height = 560,
  rootKey,
  onNodeClick,
}: {
  nodes: GraphNode[];
  edges: GraphEdgeView[];
  height?: number;
  /** `Type::id` of the selected node, drawn larger with a highlight ring. */
  rootKey?: string;
  onNodeClick?: (node: GraphNode) => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const networkRef = useRef<Network | null>(null);
  // Held in a ref so a new inline callback from the parent does not rebuild the graph.
  const clickRef = useRef(onNodeClick);
  clickRef.current = onNodeClick;

  const resolvedRoot = rootKey ?? key(nodes[0]?.type ?? "", nodes[0]?.id ?? "");
  const levels = useMemo(
    () => computeLevels(nodes, edges, resolvedRoot),
    [nodes, edges, resolvedRoot],
  );

  useEffect(() => {
    if (!ref.current || nodes.length === 0) return;

    const nodeData = new DataSet(
      nodes.map((n) => {
        const k = key(n.type, n.id);
        const isRoot = k === resolvedRoot;
        return {
          id: k,
          label: `${shorten(n.name || n.id)}\n(${n.type})`,
          title: `${n.name || n.id}\n${n.type} · ${n.id}`,
          level: levels.get(k) ?? 0,
          color: {
            background: COLORS[n.type] || "#64748b",
            border: isRoot ? "#0f172a" : "#1e293b",
            highlight: { background: COLORS[n.type] || "#64748b", border: "#0f172a" },
          },
          borderWidth: isRoot ? 4 : 1,
          font: { color: "#fff", size: isRoot ? 15 : 12 },
          shape: "box",
          margin: { top: 8, right: 10, bottom: 8, left: 10 },
        };
      }),
    );

    const edgeData = new DataSet(
      edges.map((e, i) => ({
        id: i,
        from: key(e.source_type, e.source),
        to: key(e.target_type, e.target),
        label: e.relation,
        arrows: "to",
        font: { size: 9, align: "middle", color: "#64748b", strokeWidth: 3 },
        color: { color: "#94a3b8", highlight: "#2563eb" },
      })),
    );

    const network = new Network(
      ref.current,
      { nodes: nodeData, edges: edgeData },
      {
        // Hierarchical layout is computed directly rather than simulated, so the
        // graph appears immediately instead of after a stabilization run.
        layout: {
          hierarchical: {
            enabled: true,
            direction: "LR",
            sortMethod: "directed",
            levelSeparation: 260,
            nodeSpacing: 130,
            treeSpacing: 160,
          },
        },
        physics: { enabled: false },
        interaction: { hover: true, tooltipDelay: 200, navigationButtons: true, keyboard: false },
        edges: { smooth: { enabled: true, type: "cubicBezier", roundness: 0.5 } as any },
        nodes: { widthConstraint: { maximum: 220 } },
      },
    );
    networkRef.current = network;

    network.on("click", (params) => {
      const nodeId = params.nodes?.[0];
      if (!nodeId || !clickRef.current) return;
      const raw = String(nodeId);
      const idx = raw.indexOf("::");
      clickRef.current({ type: raw.slice(0, idx), id: raw.slice(idx + 2) });
    });

    // Frame the whole lineage so the selected node is never off-screen.
    network.once("afterDrawing", () => network.fit({ animation: false }));

    return () => {
      network.destroy();
      networkRef.current = null;
    };
  }, [nodes, edges, resolvedRoot, levels]);

  return (
    <div style={{ position: "relative" }}>
      <div
        ref={ref}
        style={{
          width: "100%",
          height,
          border: "1px solid #e2e8f0",
          borderRadius: 8,
          background: "#fff",
        }}
      />
      <button
        onClick={() => networkRef.current?.fit({ animation: true })}
        style={{
          position: "absolute",
          top: 10,
          right: 10,
          padding: "5px 10px",
          fontSize: 12,
          borderRadius: 6,
          border: "1px solid #cbd5e1",
          background: "#fff",
          cursor: "pointer",
        }}
      >
        Fit to view
      </button>
    </div>
  );
}
