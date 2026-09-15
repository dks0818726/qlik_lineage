const API_BASE = (import.meta as any).env?.VITE_API_BASE_URL || "http://localhost:8000";
const WS_BASE = (import.meta as any).env?.VITE_WS_BASE_URL || "ws://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return (await res.json()) as T;
}

export interface SearchResult {
  type: string;
  id: string;
  name?: string;
}

export interface GraphNode {
  id: string;
  type: string;
  /** Human-readable label. Present on /graph/lineage responses; falls back to id. */
  name?: string;
  /** Where the node sits relative to the selected node. */
  direction?: "root" | "upstream" | "downstream";
}

export interface LineageScope {
  node: { type: string; id: string };
  nodes: GraphNode[];
  edges: GraphEdgeView[];
  counts: { nodes: number; edges: number; upstream: number; downstream: number };
}

export interface GraphEdgeView {
  source: string;
  source_type: string;
  target: string;
  target_type: string;
  relation: string;
}

export interface DocumentationReceipt {
  status: string;
  app_id: string;
  app: string;
  filename: string;
  path: string;
  bytes: number;
  sections: number;
  evidence?: string;
  model?: string;
}

export interface ImpactCandidate {
  type: string;
  id: string;
  name?: string;
}

export interface ImpactChainNode {
  type: string;
  id: string;
  name: string;
}

export interface ImpactReport {
  status: "ok" | "ambiguous" | "not_found";
  message?: string;
  candidates?: ImpactCandidate[];
  node?: { type: string; id: string; name: string };
  summary?: {
    total_impacted: number;
    by_type: Record<string, number>;
    upstream_paths: number;
    downstream_paths: number;
    truncated: boolean;
  };
  impacted?: Record<string, { id: string; name: string }[]>;
  upstream?: { depth: number; chain: ImpactChainNode[] }[];
  downstream?: { depth: number; chain: ImpactChainNode[] }[];
}

export const api = {
  health: () => request<{ status: string }>("/health"),
  search: (q: string, type?: string) =>
    request<{ count: number; results: SearchResult[] }>(
      `/search?q=${encodeURIComponent(q)}${type ? `&type=${type}` : ""}`,
    ),
  listApps: (limit = 100000) => request<{ count: number; apps: any[] }>(`/apps?limit=${limit}`),
  appDetails: (appId: string) => request<any>(`/apps/${encodeURIComponent(appId)}`),
  /** Full load script. The details payload only carries the first 2000 chars. */
  appScript: (appId: string) =>
    request<{ app_id: string; chars: number; script: string }>(
      `/apps/${encodeURIComponent(appId)}/script`,
    ),
  neighborhood: (type: string, id: string, depth = 2) =>
    request<{ nodes: GraphNode[]; edges: GraphEdgeView[] }>(
      `/graph/neighborhood?node_type=${type}&node_id=${encodeURIComponent(id)}&depth=${depth}`,
    ),
  /** Directed upstream+downstream only, with display names already resolved. */
  lineageScope: (type: string, id: string, upDepth = 3, downDepth = 3) =>
    request<LineageScope>(
      `/graph/lineage?node_type=${type}&node_id=${encodeURIComponent(id)}` +
        `&up_depth=${upDepth}&down_depth=${downDepth}`,
    ),
  upstream: (type: string, id: string, depth = 5) =>
    request<any>(`/graph/upstream?node_type=${type}&node_id=${encodeURIComponent(id)}&depth=${depth}`),
  downstream: (type: string, id: string, depth = 5) =>
    request<any>(`/graph/downstream?node_type=${type}&node_id=${encodeURIComponent(id)}&depth=${depth}`),
  impact: (type: string, id: string, depth = 5) =>
    request<any>(`/graph/impact?node_type=${type}&node_id=${encodeURIComponent(id)}&depth=${depth}`),
  /** Impact analysis by name or id, with candidate suggestions when ambiguous. */
  impactReport: (type: string, ref: string, depth = 5) =>
    request<ImpactReport>(
      `/graph/impact-report?node_type=${type}&node_ref=${encodeURIComponent(ref)}&depth=${depth}`,
    ),
  cypher: (statement: string) =>
    request<any>(`/graph/cypher`, { method: "POST", body: JSON.stringify({ statement }) }),
  ask: (question: string) =>
    request<any>(`/agent/ask`, { method: "POST", body: JSON.stringify({ question }) }),
  scan: (mode: "full" | "delta") =>
    request<any>(`/scan/run`, { method: "POST", body: JSON.stringify({ mode }) }),

  generateDocumentation: (appRef: string) =>
    request<DocumentationReceipt>(
      `/apps/${encodeURIComponent(appRef)}/documentation`,
      { method: "POST" },
    ),
  getDocumentation: (appId: string) =>
    request<any>(`/apps/${encodeURIComponent(appId)}/documentation`),
  // Markdown cannot go through `request()`, which forces a JSON content type
  // and calls res.json() - that would throw on a text/markdown body.
  getDocumentationRaw: async (appId: string): Promise<string> => {
    const res = await fetch(
      `${API_BASE}/apps/${encodeURIComponent(appId)}/documentation?format=raw`,
    );
    if (!res.ok) {
      throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
    }
    return await res.text();
  },
};

/** Save markdown to disk as a file, using a filename we already know.
 *
 * The name comes from the receipt rather than a Content-Disposition header:
 * reading that header cross-origin needs Access-Control-Expose-Headers, and
 * the frontend already has the filename, so this avoids the extra CORS setup.
 */
export function downloadMarkdown(filename: string, markdown: string): void {
  downloadText(filename, markdown, "text/markdown");
}

export function downloadText(
  filename: string,
  text: string,
  mime = "text/plain",
): void {
  const url = URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function openLineageStream(onMessage: (event: any) => void): WebSocket {
  const ws = new WebSocket(`${WS_BASE}/ws/lineage`);
  ws.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data));
    } catch {
      /* ignore */
    }
  };
  return ws;
}
