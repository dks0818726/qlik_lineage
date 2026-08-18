import { useEffect, useState } from "react";
import { api, openLineageStream } from "../api";

export function StatusBar() {
  const [health, setHealth] = useState<string>("checking…");
  const [latest, setLatest] = useState<string>("");

  useEffect(() => {
    api.health().then((h) => setHealth(h.status)).catch(() => setHealth("offline"));
    const ws = openLineageStream((evt) => setLatest(`${evt.type} @ ${evt.ts}`));
    return () => ws.close();
  }, []);

  const dotColor = health === "ok" ? "#10b981" : health === "offline" ? "#ef4444" : "#f59e0b";
  return (
    <div
      style={{
        background: "#f1f5f9",
        padding: "6px 24px",
        fontSize: 12,
        borderBottom: "1px solid #e2e8f0",
        display: "flex",
        gap: 16,
        alignItems: "center",
      }}
    >
      <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
        <span style={{ width: 8, height: 8, borderRadius: 4, background: dotColor }} />
        API: {health}
      </span>
      <span style={{ color: "#475569" }}>Realtime: {latest || "(no events yet)"}</span>
    </div>
  );
}
