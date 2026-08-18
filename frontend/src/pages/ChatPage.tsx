import { useState } from "react";
import { api } from "../api";

interface Message {
  role: "user" | "assistant";
  content: string;
  trace?: any[];
}

const SUGGESTIONS = [
  "What apps use Orders.qvd?",
  "Show full lineage for Sales Dashboard.",
  "What will be impacted if Oracle.Customers changes?",
  "Which apps depend on Finance.qvd?",
  "Show downstream dependencies for NightlyReload task.",
  "Show upstream dependencies for Sales Dashboard.",
];

export function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  async function ask(question: string) {
    if (!question.trim()) return;
    setMessages((m) => [...m, { role: "user", content: question }]);
    setInput("");
    setBusy(true);
    try {
      const r = await api.ask(question);
      setMessages((m) => [
        ...m,
        { role: "assistant", content: r.answer || "(no answer)", trace: r.trace || [] },
      ]);
    } catch (e: any) {
      setMessages((m) => [...m, { role: "assistant", content: `Error: ${e}` }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2>Copilot Chat</h2>
      <p style={{ color: "#64748b" }}>
        Read-only Qlik lineage agent powered by LiteLLM + LangGraph with tool calling.
      </p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        {SUGGESTIONS.map((s) => (
          <button key={s} onClick={() => ask(s)} style={chip} disabled={busy}>
            {s}
          </button>
        ))}
      </div>
      <div style={{ border: "1px solid #e2e8f0", borderRadius: 8, padding: 12, minHeight: 320, background: "#fff" }}>
        {messages.length === 0 && <p style={{ color: "#94a3b8" }}>Ask anything about your Qlik lineage.</p>}
        {messages.map((m, i) => (
          <div key={i} style={{ marginBottom: 12 }}>
            <div style={{ fontWeight: 700, color: m.role === "user" ? "#0c2340" : "#1d4ed8" }}>
              {m.role === "user" ? "You" : "Copilot"}
            </div>
            <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div>
            {m.trace && m.trace.length > 0 && (
              <details style={{ marginTop: 4 }}>
                <summary style={{ cursor: "pointer", color: "#64748b" }}>Tool trace ({m.trace.length} steps)</summary>
                <pre style={{ background: "#f1f5f9", padding: 8, borderRadius: 4, fontSize: 11, overflow: "auto" }}>
                  {JSON.stringify(m.trace, null, 2)}
                </pre>
              </details>
            )}
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <input
          placeholder="Ask the Lineage Copilot…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !busy && ask(input)}
          style={{ flex: 1, padding: 8 }}
        />
        <button onClick={() => ask(input)} disabled={busy}>
          {busy ? "Thinking…" : "Send"}
        </button>
      </div>
    </section>
  );
}

const chip: React.CSSProperties = {
  padding: "4px 10px",
  borderRadius: 999,
  border: "1px solid #cbd5e1",
  background: "#f8fafc",
  cursor: "pointer",
  fontSize: 12,
};