import { useCallback, useEffect, useState } from "react";
import type { AppStatus } from "../api";

export const STATUS_META: Record<
  AppStatus,
  { label: string; color: string; bg: string; border: string; help: string }
> = {
  live: {
    label: "Live",
    color: "#166534",
    bg: "#dcfce7",
    border: "#86efac",
    help: "Published to a stream, or reloaded on a schedule.",
  },
  unscheduled: {
    label: "Unscheduled",
    color: "#1e40af",
    bg: "#dbeafe",
    border: "#93c5fd",
    help: "Reloaded recently, but only by hand: not published and no enabled reload task.",
  },
  dev_copy: {
    label: "Dev copy",
    color: "#9a3412",
    bg: "#ffedd5",
    border: "#fdba74",
    help: "A duplicate (\"App(1)\") or development copy that is not published or scheduled.",
  },
  stale: {
    label: "Stale",
    color: "#475569",
    bg: "#f1f5f9",
    border: "#cbd5e1",
    help: "Not published and has not reloaded recently.",
  },
  removed: {
    label: "Removed from Qlik",
    color: "#991b1b",
    bg: "#fee2e2",
    border: "#fca5a5",
    help: "Deleted in Qlik since the last scan; it disappears from lineage on the next scan.",
  },
};

export const STATUS_ORDER: AppStatus[] = ["live", "unscheduled", "dev_copy", "stale", "removed"];

/** Statuses that are dimmed in the graph and can be hidden. */
export function isNoise(status?: string | null): boolean {
  return status === "dev_copy" || status === "stale" || status === "removed";
}

export function daysSince(iso?: string | null): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.floor((Date.now() - t) / 86_400_000));
}

export function relativeTime(iso?: string | null): string {
  const d = daysSince(iso);
  if (d === null) return "never";
  if (d === 0) return "today";
  if (d === 1) return "yesterday";
  if (d < 60) return `${d} days ago`;
  if (d < 730) return `${Math.round(d / 30)} months ago`;
  return `${(d / 365).toFixed(1)} years ago`;
}

export function AppStatusBadge({
  status,
  reason,
  size = "sm",
}: {
  status?: string | null;
  reason?: string | null;
  size?: "sm" | "md";
}) {
  const meta = status ? STATUS_META[status as AppStatus] : undefined;
  if (!meta) return null;
  return (
    <span
      title={reason ? `${meta.label}: ${reason}` : meta.help}
      style={{
        display: "inline-block",
        background: meta.bg,
        color: meta.color,
        border: `1px solid ${meta.border}`,
        borderRadius: 999,
        padding: size === "md" ? "3px 12px" : "1px 8px",
        fontSize: size === "md" ? 13 : 11,
        fontWeight: 600,
        whiteSpace: "nowrap",
        verticalAlign: "middle",
      }}
    >
      {meta.label}
    </span>
  );
}

const HIDE_KEY = "qlik-lineage.hideNoiseApps";
const HIDE_EVENT = "qlik-lineage:hide-noise";

/** "Hide dev copies & stale apps", remembered across pages and reloads.
 *
 * The default is to show them dimmed, so nothing disappears until the user asks.
 */
export function useHideNoise(): [boolean, (v: boolean) => void] {
  const [hide, setHide] = useState(() => localStorage.getItem(HIDE_KEY) === "1");
  useEffect(() => {
    const sync = () => setHide(localStorage.getItem(HIDE_KEY) === "1");
    window.addEventListener(HIDE_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(HIDE_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  const update = useCallback((v: boolean) => {
    localStorage.setItem(HIDE_KEY, v ? "1" : "0");
    setHide(v);
    window.dispatchEvent(new Event(HIDE_EVENT));
  }, []);
  return [hide, update];
}

/** Compact "12 Live · 30 Dev copy" legend. */
export function StatusCounts({ counts }: { counts: Record<string, number> }) {
  const entries = STATUS_ORDER.filter((s) => counts[s]);
  if (!entries.length) return null;
  return (
    <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
      {entries.map((s) => (
        <span key={s} title={STATUS_META[s].help} style={{ fontSize: 12, color: "#475569" }}>
          <span
            style={{
              display: "inline-block",
              width: 9,
              height: 9,
              borderRadius: 999,
              background: STATUS_META[s].border,
              marginRight: 4,
            }}
          />
          {counts[s]} {STATUS_META[s].label}
        </span>
      ))}
    </span>
  );
}
