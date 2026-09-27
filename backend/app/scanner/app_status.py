"""Classify Qlik apps as live, dev copies, stale or unscheduled from QRS metadata.

Developers routinely duplicate an app to experiment and never delete the copy.
Qlik names each duplicate ``<original>(1)``, ``<original>(2)``, ``<original>(2)(1)``
and so on, and those copies add a lot of noise to the lineage graph. QRS already
returns enough metadata to tell them apart from production apps (publish state,
last reload time, reload tasks, ``targetAppId``), so the status is computed here as
a pure function that the scanner stores and the UI and agent use.

Measured on the production site (1,704 apps): 639 apps carry a ``(n)`` suffix, none
of them are published, and only 36 have an enabled reload task.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

LIVE = "live"
DEV_COPY = "dev_copy"
STALE = "stale"
UNSCHEDULED = "unscheduled"
# Still in Postgres/Neo4j but no longer returned by QRS: deleted in Qlik since the
# last scan, which removes it for good.
REMOVED = "removed"
REMOVED_REASON = "No longer exists in Qlik; it will be removed from lineage on the next scan"

# Statuses the UI dims by default and lets the user hide.
NOISE_STATUSES = frozenset({DEV_COPY, STALE, REMOVED})

STATUS_LABELS = {
    LIVE: "Live",
    DEV_COPY: "Dev copy",
    STALE: "Stale",
    UNSCHEDULED: "Unscheduled",
    REMOVED: "Removed from Qlik",
}

# QRS returns this placeholder date for timestamps that were never set.
_NULL_YEAR = 1753
_NULL_GUID = "00000000-0000-0000-0000-000000000000"

# One or more trailing "(n)" groups, e.g. "Sales(2)" or "Sales (2)(1)".
_COPY_SUFFIX = re.compile(r"(?:\s*\(\d+\))+\s*$")

# ExecutionResult.status codes from the QRS API.
TASK_STATUS_NAMES = {
    0: "NeverStarted", 1: "Triggered", 2: "Started", 3: "Queued",
    4: "AbortInitiated", 5: "Aborting", 6: "Aborted", 7: "FinishedSuccess",
    8: "FinishedFail", 9: "Skipped", 10: "Retry", 11: "Error", 12: "Reset",
}
_FAILED_TASK_STATUSES = {"Aborted", "FinishedFail", "Error"}


@dataclass
class AppStatus:
    app_id: str
    name: str
    app_status: str
    status_reason: str
    base_name: str
    is_copy: bool
    original_app_id: str | None = None
    created_at: str | None = None
    last_reload_at: str | None = None
    days_since_reload: int | None = None
    published: bool = False
    published_at: str | None = None
    target_app_id: str | None = None
    owner_name: str | None = None
    owner_user: str | None = None
    modified_by: str | None = None
    file_size: int | None = None
    tags: list[str] = field(default_factory=list)
    task_count: int = 0
    has_enabled_task: bool = False
    last_task_status: str | None = None
    last_task_run_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -- small parsing helpers ---------------------------------------------------
def parse_qrs_time(value: Any) -> datetime | None:
    """Parse a QRS ISO timestamp; the 1753 placeholder and blanks become None."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.year <= _NULL_YEAR:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _guid(value: Any) -> str | None:
    return value if isinstance(value, str) and value and value != _NULL_GUID else None


def base_name(name: str) -> str:
    """App name without Qlik's duplicate suffixes, lower-cased for grouping."""
    return _COPY_SUFFIX.sub("", name or "").strip().lower()


def is_copy_name(name: str) -> bool:
    return bool(_COPY_SUFFIX.search(name or ""))


def _ago(days: int | None) -> str:
    if days is None:
        return "never reloaded"
    if days == 0:
        return "reloaded today"
    if days == 1:
        return "reloaded yesterday"
    return f"last reload {days}d ago"


# -- tasks -------------------------------------------------------------------
def summarize_tasks(tasks: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group reload tasks by app id, returning the latest execution per app."""
    summary: dict[str, dict[str, Any]] = {}
    for task in tasks:
        app = task.get("app") if isinstance(task.get("app"), dict) else {}
        app_id = app.get("id") or task.get("app_id")
        if not app_id:
            continue
        entry = summary.setdefault(app_id, {
            "task_count": 0, "has_enabled_task": False,
            "last_task_status": None, "last_task_run_at": None, "_last_dt": None,
        })
        entry["task_count"] += 1
        if task.get("enabled"):
            entry["has_enabled_task"] = True
        result = (task.get("operational") or {}).get("lastExecutionResult") or {}
        run_at = parse_qrs_time(result.get("stopTime")) or parse_qrs_time(result.get("startTime"))
        if run_at and (entry["_last_dt"] is None or run_at > entry["_last_dt"]):
            entry["_last_dt"] = run_at
            entry["last_task_run_at"] = _iso(run_at)
            code = result.get("status")
            entry["last_task_status"] = TASK_STATUS_NAMES.get(code, str(code) if code is not None else None)
    for entry in summary.values():
        entry.pop("_last_dt", None)
    return summary


# -- classification ----------------------------------------------------------
def classify_app(
    app: dict[str, Any],
    task_info: dict[str, Any] | None,
    *,
    now: datetime,
    stale_days: int,
    original: dict[str, Any] | None = None,
) -> AppStatus:
    """Decide the status of one app. Rules are applied in priority order:

    1. live        - published, or has an enabled task and reloaded within ``stale_days``
    2. dev_copy    - named ``X(n)`` or carries a ``targetAppId``, and not live
    3. stale       - not reloaded within ``stale_days`` (or never reloaded)
    4. unscheduled - reloaded recently, but by hand: unpublished and no enabled task
    """
    task_info = task_info or {}
    app_id = app.get("id") or app.get("app_id")
    name = app.get("name") or app_id
    owner = app.get("owner") if isinstance(app.get("owner"), dict) else {}

    reload_dt = parse_qrs_time(app.get("lastReloadTime"))
    days = max(0, (now - reload_dt).days) if reload_dt else None
    recent = days is not None and days <= stale_days
    published = bool(app.get("published"))
    has_task = bool(task_info.get("has_enabled_task"))
    last_task = task_info.get("last_task_status")
    target = _guid(app.get("targetAppId"))
    copy_named = is_copy_name(name)

    original_id = original.get("id") if original else None
    original_name = original.get("name") if original else None
    if not original_id and target:
        original_id = target

    if published:
        status = LIVE
        stream = app.get("stream") if isinstance(app.get("stream"), dict) else {}
        where = f"stream '{stream['name']}'" if stream.get("name") else "a stream"
        reason = f"Published to {where}"
        if not recent:
            reason += f"; data may be out of date ({_ago(days)})"
    elif has_task and recent:
        status = LIVE
        reason = f"Scheduled reload task, {_ago(days)}"
    elif copy_named or target:
        status = DEV_COPY
        if copy_named and original_name:
            reason = f"Copy of '{original_name}'"
        elif copy_named:
            reason = "Named like a duplicate"
        else:
            reason = "Development copy of a published app"
        reason += f"; unpublished, {'task not reloading' if has_task else 'no reload task'}, {_ago(days)}"
    elif not recent:
        status = STALE
        if has_task:
            reason = f"Has an enabled reload task but is not reloading ({_ago(days)})"
        else:
            reason = f"Unpublished, no reload task, {_ago(days)}"
    else:
        status = UNSCHEDULED
        reason = f"Reloaded manually ({_ago(days)}); unpublished and no enabled reload task"

    if last_task in _FAILED_TASK_STATUSES:
        reason += f"; last task run {last_task}"

    owner_user = None
    if owner.get("userId"):
        owner_user = f"{owner.get('userDirectory') or ''}\\{owner['userId']}".lstrip("\\")

    return AppStatus(
        app_id=app_id,
        name=name,
        app_status=status,
        status_reason=reason,
        base_name=base_name(name),
        is_copy=copy_named or (target is not None and not published),
        original_app_id=original_id if original_id != app_id else None,
        created_at=_iso(parse_qrs_time(app.get("createdDate"))),
        last_reload_at=_iso(reload_dt),
        days_since_reload=days,
        published=published,
        published_at=_iso(parse_qrs_time(app.get("publishTime"))) if published else None,
        target_app_id=target,
        owner_name=owner.get("name"),
        owner_user=owner_user,
        modified_by=app.get("modifiedByUserName"),
        file_size=app.get("fileSize"),
        tags=[t.get("name") for t in app.get("tags") or [] if isinstance(t, dict) and t.get("name")],
        task_count=int(task_info.get("task_count") or 0),
        has_enabled_task=has_task,
        last_task_status=last_task,
        last_task_run_at=task_info.get("last_task_run_at"),
    )


def _original_rank(app: dict[str, Any], tasks: dict[str, dict[str, Any]]) -> tuple:
    """Sort key for choosing the original of a family: best candidate sorts first."""
    reload_dt = parse_qrs_time(app.get("lastReloadTime"))
    return (
        is_copy_name(app.get("name") or ""),
        not app.get("published"),
        not tasks.get(app.get("id"), {}).get("has_enabled_task"),
        -(reload_dt.timestamp() if reload_dt else 0),
    )


def classify_apps(
    apps: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    *,
    stale_days: int,
    now: datetime | None = None,
) -> list[AppStatus]:
    """Classify every app, linking each duplicate to the app it was copied from.

    Apps are grouped by name with duplicate suffixes removed. The original of a group
    is the app without a suffix, preferring published apps, then apps with an enabled
    task, then the most recently reloaded. A group made up only of copies has no
    original, because the source app was probably deleted or renamed.
    """
    now = now or datetime.now(timezone.utc)
    task_summary = summarize_tasks(tasks)

    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for app in apps:
        families[base_name(app.get("name") or "")].append(app)

    originals: dict[str, dict[str, Any]] = {}
    for key, members in families.items():
        if len(members) < 2:
            continue
        best = min(members, key=lambda a: _original_rank(a, task_summary))
        if not is_copy_name(best.get("name") or ""):
            originals[key] = best

    results: list[AppStatus] = []
    for app in apps:
        app_id = app.get("id") or app.get("app_id")
        if not app_id:
            continue
        original = originals.get(base_name(app.get("name") or ""))
        if original is not None and original.get("id") == app_id:
            original = None
        results.append(classify_app(
            app, task_summary.get(app_id), now=now, stale_days=stale_days, original=original,
        ))
    return results
