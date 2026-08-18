import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";

export function ImpactAnalysisPage() {
  const [params] = useSearchParams();
  const [type, setType] = useState(params.get("type") || "Table");
  const [id, setId] = useState(params.get("id") || "");
  const [depth, setDepth] = useState(5);
  const [upstream, setUpstream] = useState<any[]>([]);
  const [downstream, setDownstream] = useState<any[]>([]);
  const [impact, setImpact] = useState<any[]>([]);
  const [error, setError] = useState("");

  async function run() {
    setError("");
    try {
      const [u, d, i] = await Promise.all([
        api.upstream(type, id, depth),
        api.downstream(type, id, depth),
        api.impact(type, id, depth),
      ]);
      setUpstream(u.chains || []);
      setDownstream(d.chains || []);
      setImpact(i.impacted || []);
    } catch (e: any) {
      setError(String(e));
    }
  }

  useEffect(() => {
    if (id) run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <section>
      <h2>Impact Analysis</h2>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <select value={type} onChange={(e) => setType(e.target.value)} style={{ padding: 8 }}>
          {["App", "QVD", "Table", "Connection", "Task"].map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <input
          placeholder="Node id (e.g. Oracle.Customers or Orders.qvd)"
          value={id}
          onChange={(e) => setId(e.target.value)}
          style={{ flex: 1, padding: 8 }}
        />
        <input
          type="number"
          min={1}
          max={20}
          value={depth}
          onChange={(e) => setDepth(Number(e.target.value))}
          style={{ width: 80, padding: 8 }}
        />
        <button onClick={run}>Analyze</button>
      </div>
      {error && <p style={{ color: "crimson" }}>{error}</p>}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
        <Panel title={`Upstream (${upstream.length})`} items={upstream.map((r) => r.chain?.map((n: any) => `${n.type}:${n.id}`).join(" → "))} />
        <Panel title={`Downstream (${downstream.length})`} items={downstream.map((r) => r.chain?.map((n: any) => `${n.type}:${n.id}`).join(" → "))} />
        <Panel title={`Impacted nodes (${impact.length})`} items={impact.map((r) => `${r.type}:${r.id}`)} />
      </div>
    </section>
  );
}

function Panel({ title, items }: { title: string; items: string[] }) {
  return (
    <div style={{ border: "1px solid #e2e8f0", borderRadius: 8, padding: 12, background: "#fff" }}>
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      <ul style={{ paddingLeft: 16, margin: 0, fontFamily: "monospace", fontSize: 12 }}>
        {items.length ? items.map((s, i) => <li key={i}>{s}</li>) : <li style={{ color: "#94a3b8" }}>(none)</li>}
      </ul>
    </div>
  );
}
