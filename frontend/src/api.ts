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
}

export interface GraphEdgeView {
  source: string;
  source_type: string;
  target: string;
  target_type: string;
  relation: string;
}

export const api = {
  health: () => request<{ status: string }>("/health"),
  search: (q: string, type?: string) =>
    request<{ count: number; results: SearchResult[] }>(
      `/search?q=${encodeURIComponent(q)}${type ? `&type=${type}` : ""}`,
    ),
  listApps: () => request<{ count: number; apps: any[] }>(`/apps?limit=200`),
  appDetails: (appId: string) => request<any>(`/apps/${encodeURIComponent(appId)}`),
  neighborhood: (type: string, id: string, depth = 2) =>
    request<{ nodes: GraphNode[]; edges: GraphEdgeView[] }>(
      `/graph/neighborhood?node_type=${type}&node_id=${encodeURIComponent(id)}&depth=${depth}`,
    ),
  upstream: (type: string, id: string, depth = 5) =>
    request<any>(`/graph/upstream?node_type=${type}&node_id=${encodeURIComponent(id)}&depth=${depth}`),
  downstream: (type: string, id: string, depth = 5) =>
    request<any>(`/graph/downstream?node_type=${type}&node_id=${encodeURIComponent(id)}&depth=${depth}`),
  impact: (type: string, id: string, depth = 5) =>
    request<any>(`/graph/impact?node_type=${type}&node_id=${encodeURIComponent(id)}&depth=${depth}`),
  cypher: (statement: string) =>
    request<any>(`/graph/cypher`, { method: "POST", body: JSON.stringify({ statement }) }),
  ask: (question: string) =>
    request<any>(`/agent/ask`, { method: "POST", body: JSON.stringify({ question }) }),
  scan: (mode: "full" | "delta") =>
    request<any>(`/scan/run`, { method: "POST", body: JSON.stringify({ mode }) }),
};

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
