"""Live, read-only view of Qlik reload tasks for the Copilot agent.

Task status changes minute to minute, so it is never stored: the lineage store
keeps only task names and structural links (RUNS, TRIGGERS) for impact analysis.
Questions like "did the sales task fail?" or "what is running now?" are answered
by reading QRS at question time through this module.

Guardrails:
* QRS access goes through ``QrsClient``, which only allows GETs on an allow-list
  of read endpoints - nothing here can start, stop or edit a task.
* User text never reaches a QRS filter. Task names are resolved to GUIDs via
  Postgres; only validated GUIDs and timestamps generated here are interpolated.
* Look-back windows and row counts are clamped, messages are truncated and
  credential-like values redacted, and internal fields (script log locations,
  node ids, users) are dropped before anything reaches the LLM.
* Reads are cached briefly so a burst of chat questions does not hammer QRS,
  and a QRS failure is reported as ``unavailable`` instead of guessed around.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.scanner.app_status import TASK_STATUS_NAMES, parse_qrs_time

logger = logging.getLogger(__name__)

GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
FAILED_STATUSES = (6, 8, 11)  # Aborted, FinishedFail, Error
RUNNING_STATUSES = frozenset({1, 2, 3, 4, 5, 10})

FAILURE_HOURS = (1, 168)
UPCOMING_HOURS = (1, 48)
MAX_MESSAGE_CHARS = 300
RECENT_RUNS = 5

INCREMENT_OPTIONS = {0: "once", 1: "hourly", 2: "daily", 3: "weekly", 4: "monthly", 5: "custom"}

_SECRET_RE = re.compile(r"(?i)\b(password|passwd|pwd|secret|token|apikey|api_key)\s*[=:]\s*[^;,\s]+")
# Scheduler bookkeeping lines carry no diagnostic value for a user.
_NOISE_RE = re.compile(
    r"^(Changing task state|Trying to start task|Task finished|Reference to scriptlog|Max retries reached)",
    re.IGNORECASE,
)


def clamp(value: Any, bounds: tuple[int, int], default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(bounds[0], min(bounds[1], number))


def clean_message(message: Any) -> str:
    text = _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", str(message or "")).strip()
    return text if len(text) <= MAX_MESSAGE_CHARS else text[: MAX_MESSAGE_CHARS - 1] + "…"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@dataclass
class LiveTaskMonitor:
    qrs: Any
    repository: Any
    cache_seconds: int = 30
    max_rows: int = 25
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    _cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- public tools -------------------------------------------------------------
    def task_status(self, task: str) -> dict[str, Any]:
        """Current state of one task: last run, next run, schedules, chains, recent runs."""
        resolved = self._resolve_task(task)
        if "task_id" not in resolved:
            return resolved
        task_id = resolved["task_id"]
        try:
            found = self._read("/qrs/reloadtask/full", f"id eq {task_id}")
            if not found:
                return {"status": "not_found",
                        "message": f"Task {task_id} no longer exists in Qlik (it may have been deleted)."}
            raw = found[0]
            schedules = self._read("/qrs/schemaevent/full", f"reloadTask.id eq {task_id}")
            upstream = self._read("/qrs/compositeevent/full", f"reloadTask.id eq {task_id}")
            downstream = self._read("/qrs/compositeevent/full", f"compositeRules.reloadTask.id eq {task_id}")
            runs = self._read("/qrs/executionresult/full", f"taskID eq {task_id}")
            sessions = self._read("/qrs/executionsession/full")
        except Exception as exc:  # noqa: BLE001 - reported to the user, never guessed around
            return self._unavailable(exc)

        now = self.now()
        operational = raw.get("operational") or {}
        last = operational.get("lastExecutionResult") or {}
        running = any(((s.get("reloadTask") or {}).get("id") == task_id) for s in sessions)
        runs = sorted(runs, key=lambda r: r.get("startTime") or "", reverse=True)[:RECENT_RUNS]
        return {
            "status": "ok",
            "as_of": _iso(now),
            "task": {
                "id": task_id,
                "name": raw.get("name"),
                "enabled": bool(raw.get("enabled")),
                "app": {"id": (raw.get("app") or {}).get("id"), "name": (raw.get("app") or {}).get("name")},
            },
            "running_now": running,
            "last_run": self._run_summary(last, now) if last else None,
            "next_run": self._when(operational.get("nextExecution"), now),
            "schedules": [self._schedule(s, now) for s in schedules][: self.max_rows],
            "triggered_by": [self._trigger_upstream(e) for e in upstream][: self.max_rows],
            "triggers": [
                {"task": (e.get("reloadTask") or {}).get("name"), "task_id": (e.get("reloadTask") or {}).get("id"),
                 "enabled": bool(e.get("enabled"))}
                for e in downstream
            ][: self.max_rows],
            "recent_runs": [self._run_summary(r, now) for r in runs],
        }

    def running_tasks(self) -> dict[str, Any]:
        """Tasks executing on the Qlik scheduler right now."""
        try:
            sessions = self._read("/qrs/executionsession/full")
        except Exception as exc:  # noqa: BLE001
            return self._unavailable(exc)
        now = self.now()
        rows = []
        for s in sessions:
            result = s.get("executionResult") or {}
            started = parse_qrs_time(result.get("startTime"))
            rows.append({
                "task": (s.get("reloadTask") or {}).get("name"),
                "task_id": (s.get("reloadTask") or {}).get("id"),
                "app": (s.get("app") or {}).get("name"),
                "state": TASK_STATUS_NAMES.get(result.get("status"), str(result.get("status"))),
                "started": _iso(started) if started else None,
                "minutes_running": round((now - started).total_seconds() / 60, 1) if started else None,
                "retries": s.get("retries", 0),
            })
        rows.sort(key=lambda r: r["minutes_running"] or 0, reverse=True)
        return {"status": "ok", "as_of": _iso(now), "running_count": len(rows),
                "tasks": rows[: self.max_rows], "truncated": len(rows) > self.max_rows}

    def task_failures(self, hours: int = 24) -> dict[str, Any]:
        """Runs that ended Aborted / FinishedFail / Error in the last ``hours``, one row per task."""
        hours = clamp(hours, FAILURE_HOURS, 24)
        now = self.now()
        since = now - timedelta(hours=hours)
        statuses = " or ".join(f"status eq {s}" for s in FAILED_STATUSES)
        try:
            results = self._read("/qrs/executionresult/full", f"startTime ge '{_iso(since)}' and ({statuses})")
        except Exception as exc:  # noqa: BLE001
            return self._unavailable(exc)

        grouped: dict[str, dict[str, Any]] = {}
        for r in sorted(results, key=lambda x: x.get("startTime") or "", reverse=True):
            task_id = r.get("taskID") or "unknown"
            entry = grouped.get(task_id)
            if entry is None:
                grouped[task_id] = {
                    "task_id": task_id,
                    "app_id": r.get("appID"),
                    "failures": 1,
                    "last_status": TASK_STATUS_NAMES.get(r.get("status"), str(r.get("status"))),
                    "last_failed_at": r.get("startTime"),
                    "last_messages": self._messages(r),
                }
            else:
                entry["failures"] += 1

        rows = sorted(grouped.values(), key=lambda e: e["last_failed_at"] or "", reverse=True)
        self._attach_names(rows)
        return {"status": "ok", "as_of": _iso(now), "window_hours": hours,
                "failed_runs": len(results), "failed_tasks": len(rows),
                "tasks": rows[: self.max_rows], "truncated": len(rows) > self.max_rows}

    def upcoming_tasks(self, hours: int = 6) -> dict[str, Any]:
        """Enabled tasks whose next scheduled run falls within the next ``hours``."""
        hours = clamp(hours, UPCOMING_HOURS, 6)
        now = self.now()
        until = now + timedelta(hours=hours)
        try:
            tasks = self._read(
                "/qrs/reloadtask/full",
                f"enabled eq true and operational.nextExecution ge '{_iso(now)}' "
                f"and operational.nextExecution le '{_iso(until)}'",
            )
        except Exception as exc:  # noqa: BLE001
            return self._unavailable(exc)
        rows = []
        for t in tasks:
            nxt = parse_qrs_time((t.get("operational") or {}).get("nextExecution"))
            if nxt is None:
                continue
            rows.append({
                "task": t.get("name"),
                "task_id": t.get("id"),
                "app": (t.get("app") or {}).get("name"),
                "next_run": _iso(nxt),
                "in_minutes": round((nxt - now).total_seconds() / 60),
            })
        rows.sort(key=lambda r: r["next_run"])
        return {"status": "ok", "as_of": _iso(now), "window_hours": hours, "scheduled_count": len(rows),
                "tasks": rows[: self.max_rows], "truncated": len(rows) > self.max_rows}

    # -- helpers ------------------------------------------------------------------
    def _read(self, path: str, filter_expr: str | None = None) -> list[dict[str, Any]]:
        key = (path, filter_expr or "")
        with self._lock:
            hit = self._cache.get(key)
            if hit and self.clock() - hit[0] < self.cache_seconds:
                return hit[1]
        data = self.qrs.read(path, filter_expr)
        with self._lock:
            if len(self._cache) > 256:
                self._cache.clear()
            self._cache[key] = (self.clock(), data)
        return data

    def _resolve_task(self, task: Any) -> dict[str, Any]:
        text = str(task or "").strip()
        if not text:
            return {"status": "error", "message": "Give a task name or task id."}
        if GUID_RE.match(text):
            return {"task_id": text.lower()}
        matches = self.repository.search_nodes(text[:200], type_filter="Task", limit=10)
        if not matches:
            return {"status": "not_found",
                    "message": f"No task matches '{text}'. Tasks are known by name after a scan."}
        exact = [m for m in matches if (m.get("name") or "").lower() == text.lower()]
        if len(exact) == 1:
            return {"task_id": exact[0]["id"]}
        if len(matches) == 1:
            return {"task_id": matches[0]["id"]}
        return {"status": "ambiguous",
                "message": "Several tasks match; ask the user which one, or retry with the task id.",
                "candidates": [{"task_id": m["id"], "name": m.get("name")} for m in (exact or matches)]}

    def _attach_names(self, rows: list[dict[str, Any]]) -> None:
        try:
            names = self.repository.display_names({
                "Task": [r["task_id"] for r in rows],
                "App": [r["app_id"] for r in rows if r.get("app_id")],
            })
        except Exception:  # noqa: BLE001 - names are a nicety; ids still identify the task
            logger.exception("Task name lookup failed")
            names = {}
        for r in rows:
            r["task"] = names.get(f"Task::{r['task_id']}")
            r["app"] = names.get(f"App::{r.get('app_id')}")

    def _run_summary(self, result: dict[str, Any], now: datetime) -> dict[str, Any]:
        started = parse_qrs_time(result.get("startTime"))
        stopped = parse_qrs_time(result.get("stopTime"))
        duration_ms = result.get("duration") or 0
        return {
            "status": TASK_STATUS_NAMES.get(result.get("status"), str(result.get("status"))),
            "started": _iso(started) if started else None,
            "finished": _iso(stopped) if stopped else None,
            "hours_ago": round((now - started).total_seconds() / 3600, 1) if started else None,
            "duration_minutes": round(duration_ms / 60000, 1) if duration_ms else None,
            "messages": self._messages(result),
        }

    @staticmethod
    def _messages(result: dict[str, Any], limit: int = 2) -> list[str]:
        details = sorted(result.get("details") or [], key=lambda d: d.get("detailCreatedDate") or "", reverse=True)
        useful = [d.get("message") for d in details if d.get("message") and not _NOISE_RE.match(d["message"])]
        return [clean_message(m) for m in useful[:limit]]

    @staticmethod
    def _when(value: Any, now: datetime) -> dict[str, Any] | None:
        dt = parse_qrs_time(value)
        if dt is None:
            return None
        return {"at": _iso(dt), "in_minutes": round((dt - now).total_seconds() / 60)}

    def _schedule(self, event: dict[str, Any], now: datetime) -> dict[str, Any]:
        return {
            "name": event.get("name"),
            "enabled": bool(event.get("enabled")),
            "repeat": INCREMENT_OPTIONS.get(event.get("incrementOption"), str(event.get("incrementOption"))),
            "increment": event.get("incrementDescription"),  # "minutes hours days weeks"
            "filter": event.get("schemaFilterDescription"),
            "time_zone": event.get("timeZone"),
            "starts": event.get("startDate"),
            "expires": event.get("expirationDate"),
            "next_run": self._when((event.get("operational") or {}).get("nextExecution"), now),
        }

    @staticmethod
    def _trigger_upstream(event: dict[str, Any]) -> dict[str, Any]:
        rules = event.get("compositeRules") or []
        return {
            "trigger": event.get("name"),
            "enabled": bool(event.get("enabled")),
            "requires": "all" if len(rules) > 1 else "one",
            "after": [
                {"task": (r.get("reloadTask") or {}).get("name"),
                 "task_id": (r.get("reloadTask") or {}).get("id"),
                 "on": {1: "success", 2: "failure"}.get(r.get("ruleState"), "unknown")}
                for r in rules
            ],
        }

    @staticmethod
    def _unavailable(exc: Exception) -> dict[str, Any]:
        logger.warning("Live task lookup failed: %s", exc)
        return {"status": "unavailable",
                "message": "Live task data could not be read from Qlik right now. Do not guess; "
                           "tell the user to retry shortly or check the QMC."}
