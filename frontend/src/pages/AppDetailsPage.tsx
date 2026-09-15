import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, downloadMarkdown, downloadText, GraphEdgeView, GraphNode } from "../api";
import { LineageGraph } from "../components/LineageGraph";

function DocumentationPanel({ appId }: { appId: string }) {
  const [doc, setDoc] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState(false);

  // A 404 here is the normal "not generated yet" state, not a failure.
  useEffect(() => {
    setDoc(null);
    setPreview(false);
    setError("");
    api.getDocumentation(appId).then(setDoc).catch(() => setDoc(null));
  }, [appId]);

  async function generate() {
    setBusy(true);
    setError("");
    try {
      await api.generateDocumentation(appId);
      setDoc(await api.getDocumentation(appId));
    } catch (e: any) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h3>Documentation</h3>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button onClick={generate} disabled={busy}>
          {busy ? "Generating…" : doc ? "Regenerate" : "Generate documentation"}
        </button>
        {doc && (
          <>
            <button onClick={() => setPreview((p) => !p)}>
              {preview ? "Hide" : "Preview"}
            </button>
            <button onClick={() => downloadMarkdown(doc.filename, doc.markdown)}>
              Download .md
            </button>
            <span style={{ color: "#64748b", fontSize: 12 }}>
              {doc.sections} sections · generated {String(doc.generated_at).slice(0, 16)}
            </span>
            {/* The stored script hash no longer matches the app's current
                hash, so this document describes an older script. */}
            {doc.stale && (
              <span style={staleBadge}>out of date — regenerate</span>
            )}
          </>
        )}
      </div>
      {error && <p style={{ color: "crimson" }}>{error}</p>}
      {!doc && !busy && !error && (
        <p style={{ color: "#94a3b8" }}>No documentation generated yet.</p>
      )}
      {doc && preview && (
        <pre
          style={{
            whiteSpace: "pre-wrap",
            background: "#f8fafc",
            border: "1px solid #e2e8f0",
            borderRadius: 8,
            padding: 12,
            maxHeight: 420,
            overflow: "auto",
            fontSize: 12,
          }}
        >
          {doc.markdown}
        </pre>
      )}
    </>
  );
}

const staleBadge: React.CSSProperties = {
  background: "#fef3c7",
  color: "#92400e",
  border: "1px solid #fcd34d",
  borderRadius: 999,
  padding: "2px 10px",
  fontSize: 12,
};

/** Shows the excerpt by default and loads the full script on demand.
 *
 * The details endpoint caps the script at 2000 characters, which is a small
 * fraction of a typical script. Fetching the rest separately keeps the page
 * load light - the largest stored script is several megabytes.
 */
function ScriptPanel({
  appId,
  appName,
  excerpt,
  chars,
  truncated,
}: {
  appId: string;
  appName?: string;
  excerpt: string;
  chars?: number;
  truncated?: boolean;
}) {
  const [full, setFull] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setFull(null);
    setExpanded(false);
    setError("");
  }, [appId]);

  async function load(thenExpand: boolean) {
    if (full) {
      setExpanded(thenExpand);
      return full;
    }
    setBusy(true);
    setError("");
    try {
      const r = await api.appScript(appId);
      setFull(r.script);
      setExpanded(thenExpand);
      return r.script;
    } catch (e: any) {
      setError(String(e));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function download() {
    const text = await load(expanded);
    if (text === null) return;
    const safe = (appName || appId).replace(/[^\w.-]+/g, "_");
    downloadText(`${safe}.qvs`, text);
  }

  const shown = expanded && full ? full : excerpt;
  const isPartial = !expanded && truncated;

  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 12,
          marginBottom: 8,
        }}
      >
        <h3 style={{ margin: 0 }}>
          Load script{isPartial ? " (excerpt)" : ""}
        </h3>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {typeof chars === "number" && chars > 0 && (
            <span style={{ fontSize: 12, color: "#64748b" }}>
              {isPartial
                ? `showing ${excerpt.length.toLocaleString()} of ${chars.toLocaleString()} characters`
                : `${chars.toLocaleString()} characters`}
            </span>
          )}
          {truncated && (
            <button onClick={() => (expanded ? setExpanded(false) : load(true))} disabled={busy}>
              {busy ? "Loading…" : expanded ? "Show less" : "Show full script"}
            </button>
          )}
          {chars ? (
            <button onClick={download} disabled={busy}>
              Download .qvs
            </button>
          ) : null}
        </div>
      </div>

      {error && <p style={{ color: "crimson" }}>{error}</p>}

      <pre
        style={{
          background: "#0f172a",
          color: "#e2e8f0",
          padding: 12,
          borderRadius: 8,
          maxHeight: expanded ? 640 : 320,
          overflow: "auto",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          fontSize: 12.5,
          lineHeight: 1.5,
        }}
      >
        {shown || "(empty)"}
      </pre>

      {isPartial && (
        <p style={{ color: "#64748b", fontSize: 12, marginTop: 6 }}>
          Truncated for page load — use “Show full script” to see the rest.
        </p>
      )}
    </>
  );
}

export function AppDetailsPage() {
  const { appId } = useParams();
  const navigate = useNavigate();
  const [details, setDetails] = useState<any>(null);
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdgeView[]>([]);
  const [counts, setCounts] = useState<{ upstream: number; downstream: number } | null>(null);
  const [depth, setDepth] = useState(2);
  const [graphLoading, setGraphLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!appId) return;
    api
      .appDetails(appId)
      .then(setDetails)
      .catch((e) => setError(String(e)));
  }, [appId]);

  // `/graph/lineage` replaces the old undirected `/graph/neighborhood` call: it walks
  // upstream and downstream only, and resolves display names server-side so the graph
  // can label nodes instead of showing raw GUIDs and QVD paths.
  useEffect(() => {
    if (!appId) return;
    setGraphLoading(true);
    api
      .lineageScope("App", appId, depth, depth)
      .then((r) => {
        setNodes(r.nodes);
        setEdges(r.edges);
        setCounts({ upstream: r.counts.upstream, downstream: r.counts.downstream });
      })
      .catch(() => {
        setNodes([]);
        setEdges([]);
        setCounts(null);
      })
      .finally(() => setGraphLoading(false));
  }, [appId, depth]);

  // Clicking another app in the graph opens that app's page, so the graph doubles
  // as a way to walk the lineage without going back to the Graph tab.
  const handleNodeClick = useCallback(
    (n: GraphNode) => {
      if (n.type === "App" && n.id !== appId) navigate(`/app/${encodeURIComponent(n.id)}`);
    },
    [appId, navigate],
  );

  if (error) return <p style={{ color: "crimson" }}>{error}</p>;
  if (!details) return <p>Loading…</p>;

  return (
    <section>
      <h2>App Details: {details.app?.name || appId}</h2>
      <table style={{ marginBottom: 16 }}>
        <tbody>
          <tr>
            <td style={{ paddingRight: 16 }}><b>App ID</b></td>
            <td>{details.app?.app_id}</td>
          </tr>
          <tr>
            <td><b>Owner</b></td>
            <td>{details.app?.owner_id || "—"}</td>
          </tr>
          <tr>
            <td><b>Stream</b></td>
            <td>{details.app?.stream_id || "—"}</td>
          </tr>
          <tr>
            <td><b>Script hash</b></td>
            <td style={{ fontFamily: "monospace" }}>{details.app?.script_hash || "—"}</td>
          </tr>
          <tr>
            <td><b>Last modified</b></td>
            <td>{details.app?.modified_at || "—"}</td>
          </tr>
        </tbody>
      </table>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 12,
          marginBottom: 8,
        }}
      >
        <h3 style={{ margin: 0 }}>Lineage neighborhood</h3>
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
      </div>

      {!graphLoading && counts && nodes.length > 0 && (
        <p style={{ color: "#475569", fontSize: 13, margin: "0 0 10px" }}>
          {counts.upstream} upstream · {counts.downstream} downstream · {edges.length}{" "}
          relationships. Sources sit to the left, consumers to the right. Click another app
          to open it.
        </p>
      )}

      {graphLoading ? (
        <p style={{ color: "#64748b" }}>Loading lineage…</p>
      ) : nodes.length > 0 ? (
        <LineageGraph
          nodes={nodes}
          edges={edges}
          height={420}
          rootKey={`App::${appId}`}
          onNodeClick={handleNodeClick}
        />
      ) : (
        <p style={{ color: "#64748b" }}>No upstream or downstream lineage found for this app.</p>
      )}

      <DocumentationPanel appId={details.app?.app_id || appId!} />

      <ScriptPanel
        appId={details.app?.app_id || appId!}
        appName={details.app?.name}
        excerpt={details.script_excerpt || ""}
        chars={details.script_chars}
        truncated={details.script_truncated}
      />
    </section>
  );
}
