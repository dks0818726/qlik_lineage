import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, downloadMarkdown } from "../api";
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

export function AppDetailsPage() {
  const { appId } = useParams();
  const [details, setDetails] = useState<any>(null);
  const [graph, setGraph] = useState<{ nodes: any[]; edges: any[] }>({ nodes: [], edges: [] });
  const [error, setError] = useState("");

  useEffect(() => {
    if (!appId) return;
    api
      .appDetails(appId)
      .then(setDetails)
      .catch((e) => setError(String(e)));
    api
      .neighborhood("App", appId, 2)
      .then(setGraph)
      .catch(() => {});
  }, [appId]);

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

      <h3>Lineage neighborhood</h3>
      <LineageGraph nodes={graph.nodes} edges={graph.edges} height={420} />

      <DocumentationPanel appId={details.app?.app_id || appId!} />

      <h3>Load script (excerpt)</h3>
      <pre style={{ background: "#0f172a", color: "#e2e8f0", padding: 12, borderRadius: 8, maxHeight: 320, overflow: "auto" }}>
        {details.script_excerpt || "(empty)"}
      </pre>
    </section>
  );
}
