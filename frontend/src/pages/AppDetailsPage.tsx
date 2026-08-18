import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { LineageGraph } from "../components/LineageGraph";

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

      <h3>Load script (excerpt)</h3>
      <pre style={{ background: "#0f172a", color: "#e2e8f0", padding: 12, borderRadius: 8, maxHeight: 320, overflow: "auto" }}>
        {details.script_excerpt || "(empty)"}
      </pre>
    </section>
  );
}
