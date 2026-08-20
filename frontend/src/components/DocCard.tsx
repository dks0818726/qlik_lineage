import { useState } from "react";
import { DocumentationReceipt, api, downloadMarkdown } from "../api";

/** Pull a documentation receipt out of an agent trace.
 *
 * Written defensively: the card is driven by the trace shape emitted by
 * `_node_tools` today (`{step, tool, args, result}`). If that format ever
 * changes this returns null and the card simply does not render, rather than
 * throwing and taking the whole chat message down with it.
 */
export function findDocReceipt(trace?: any[]): DocumentationReceipt | null {
  if (!Array.isArray(trace)) return null;
  for (const entry of trace) {
    if (!entry || typeof entry !== "object") continue;
    if (entry.tool !== "generate_app_documentation") continue;
    const r = entry.result;
    if (r && typeof r === "object" && r.status === "written" && r.app_id && r.filename) {
      return r as DocumentationReceipt;
    }
  }
  return null;
}

function DocCard({ receipt }: { receipt: DocumentationReceipt }) {
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The markdown is fetched only when the user asks for it, so it never
  // travels through the conversation and never inflates the token cost of
  // subsequent turns.
  async function fetchMarkdown(): Promise<string> {
    return await api.getDocumentationRaw(receipt.app_id);
  }

  async function onPreview() {
    if (preview) {
      setPreview(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setPreview(await fetchMarkdown());
    } catch (e: any) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDownload() {
    setBusy(true);
    setError(null);
    try {
      downloadMarkdown(receipt.filename, await fetchMarkdown());
    } catch (e: any) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const kb = (receipt.bytes / 1024).toFixed(1);
  return (
    <div style={card}>
      <div style={{ fontWeight: 600 }}>📄 {receipt.app} — documentation ready</div>
      <div style={{ color: "#64748b", fontSize: 12, margin: "2px 0 8px" }}>
        {receipt.sections} sections · {kb} KB
        {receipt.evidence ? ` · ${receipt.evidence}` : ""}
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button onClick={onPreview} disabled={busy} style={btn}>
          {preview ? "Hide preview" : busy ? "Loading…" : "Preview"}
        </button>
        <button onClick={onDownload} disabled={busy} style={btn}>
          Download .md
        </button>
      </div>
      {error && <div style={{ color: "#b91c1c", fontSize: 12, marginTop: 6 }}>{error}</div>}
      {preview && (
        <pre
          style={{
            whiteSpace: "pre-wrap",
            background: "#f8fafc",
            border: "1px solid #e2e8f0",
            borderRadius: 4,
            padding: 8,
            marginTop: 8,
            maxHeight: 420,
            overflow: "auto",
            fontSize: 12,
          }}
        >
          {preview}
        </pre>
      )}
    </div>
  );
}

const card: React.CSSProperties = {
  border: "1px solid #cbd5e1",
  borderRadius: 8,
  padding: 12,
  marginTop: 8,
  background: "#f8fafc",
};

const btn: React.CSSProperties = {
  padding: "4px 12px",
  borderRadius: 6,
  border: "1px solid #cbd5e1",
  background: "#fff",
  cursor: "pointer",
  fontSize: 12,
};

export default DocCard;
