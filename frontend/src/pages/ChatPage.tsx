import { useEffect, useRef } from "react";
import { api } from "../api";
import DocCard, { findDocReceipt } from "../components/DocCard";
import { askQuestion, clearChat, setDraft, useChatSession } from "../chatSession";

const SUGGESTIONS = [
  "What apps use Orders.qvd?",
  "Show full lineage for Sales Dashboard.",
  "What will be impacted if Oracle.Customers changes?",
  "Which apps depend on Finance.qvd?",
  "Show downstream dependencies for NightlyReload task.",
  "Show upstream dependencies for Sales Dashboard.",
  "Document the ISS Extract Archive app.",
];

export function ChatPage() {
  // Conversation state is held in the session store, not in this component, so it
  // survives unmounting when the user switches to another tab and comes back.
  const { messages, busy, draft } = useChatSession();
  const endRef = useRef<HTMLDivElement | null>(null);

  const ask = (question: string) => askQuestion(question, api.ask);

  // Keep the newest message in view, including after returning to this tab.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, busy]);

  function onNewChat() {
    if (messages.length > 0 && !window.confirm("Clear this conversation and start a new chat?")) {
      return;
    }
    clearChat();
  }

  return (
    <section>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
        <h2 style={{ marginBottom: 4 }}>Copilot Chat</h2>
        {messages.length > 0 && (
          <span style={{ color: "#94a3b8", fontSize: 12 }}>
            {messages.filter((m) => m.role === "user").length} question
            {messages.filter((m) => m.role === "user").length === 1 ? "" : "s"} this session
          </span>
        )}
        <button onClick={onNewChat} disabled={busy || messages.length === 0} style={newChatBtn}>
          New chat
        </button>
      </div>
      <p style={{ color: "#64748b", marginTop: 4 }}>
        Read-only Qlik lineage agent powered by LiteLLM + LangGraph with tool calling.
        Your conversation stays in this browser tab and is cleared when you close it.
      </p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        {SUGGESTIONS.map((s) => (
          <button key={s} onClick={() => ask(s)} style={chip} disabled={busy}>
            {s}
          </button>
        ))}
      </div>
      <div style={{ border: "1px solid #e2e8f0", borderRadius: 8, padding: 12, minHeight: 320, maxHeight: 520, overflowY: "auto", background: "#fff" }}>
        {messages.length === 0 && <p style={{ color: "#94a3b8" }}>Ask anything about your Qlik lineage.</p>}
        {messages.map((m, i) => (
          <div key={i} style={{ marginBottom: 12 }}>
            <div style={{ fontWeight: 700, color: m.role === "user" ? "#0c2340" : "#1d4ed8" }}>
              {m.role === "user" ? "You" : "Copilot"}
            </div>
            <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div>
            {findDocReceipt(m.trace) && <DocCard receipt={findDocReceipt(m.trace)!} />}
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
        {busy && <div style={{ color: "#94a3b8", fontStyle: "italic" }}>Copilot is thinking…</div>}
        <div ref={endRef} />
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <input
          placeholder="Ask the Lineage Copilot…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !busy && ask(draft)}
          style={{ flex: 1, padding: 8 }}
        />
        <button onClick={() => ask(draft)} disabled={busy}>
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

const newChatBtn: React.CSSProperties = {
  marginLeft: "auto",
  padding: "4px 12px",
  borderRadius: 6,
  border: "1px solid #cbd5e1",
  background: "#f8fafc",
  cursor: "pointer",
  fontSize: 12,
};