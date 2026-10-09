from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.graph.neo4j_client import Neo4jClient
from app.storage.repository import PostgresRepository

logger = logging.getLogger(__name__)


# OpenAI/LiteLLM-compatible tool schema. The agent invokes these by name.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_apps",
            "description": "Search Qlik apps by partial name or id.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_qvds",
            "description": "Search QVD files by partial path.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qvd_variants",
            "description": (
                "PREFERRED when the user names a QVD by filename. Returns every QVD node "
                "whose path ends with that filename, each with its reader and writer counts, "
                "plus the combined number of distinct reader apps across all variants. "
                "The same physical file often exists under several ids because some paths "
                "contain an unresolved Qlik variable such as $(vServer); querying only one "
                "id can understate impact by more than 20x. Use this before impact/upstream "
                "when the user says something like 'e_date_dim.qvd'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "QVD filename, e.g. 'e_date_dim.qvd'. A full path also works.",
                    }
                },
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_tables",
            "description": "Search source database tables by name.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_tasks",
            "description": "Search Qlik reload tasks by name or id.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upstream",
            "description": "Return upstream lineage chains for a node (what feeds it).",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {"type": "string", "enum": ["App", "QVD", "Table", "Connection", "Task"]},
                    "node_id": {"type": "string"},
                    "depth": {"type": "integer", "default": 5},
                },
                "required": ["node_type", "node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "downstream",
            "description": "Return downstream dependents (what consumes this node).",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {"type": "string", "enum": ["App", "QVD", "Table", "Connection", "Task"]},
                    "node_id": {"type": "string"},
                    "depth": {"type": "integer", "default": 5},
                },
                "required": ["node_type", "node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "impact",
            "description": "Return distinct downstream nodes impacted if the given node changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {"type": "string"},
                    "node_id": {"type": "string"},
                    "depth": {"type": "integer", "default": 5},
                },
                "required": ["node_type", "node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_cypher",
            "description": "Run a READ-ONLY Cypher query against Neo4j. Write keywords are rejected.",
            "parameters": {
                "type": "object",
                "properties": {"statement": {"type": "string"}},
                "required": ["statement"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_app_documentation",
            "description": (
                "Write a full technical documentation file explaining what a Qlik app "
                "does - its purpose, data sources, transformations, outputs and "
                "dependencies. Use this when the user asks to document an app, or asks "
                "what an app does and wants a written document. Accepts either the app "
                "name or the app id. This is expensive: call it at most once per "
                "request, and never just to answer a short factual question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "App name (e.g. 'Sales Dashboard') or app id.",
                    }
                },
                "required": ["app"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "live_task_status",
            "description": (
                "LIVE from Qlik: current state of one reload task - last run result and "
                "error message, next scheduled run, whether it is running now, its "
                "schedules (triggers), which tasks start it and which it starts, and the "
                "last 5 runs. Accepts a task name or task id. Read-only."
            ),
            "parameters": {
                "type": "object",
                "properties": {"task": {"type": "string", "description": "Task name or task id."}},
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "live_running_tasks",
            "description": "LIVE from Qlik: reload tasks executing right now and how long they have run.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "live_task_failures",
            "description": (
                "LIVE from Qlik: tasks whose runs ended Aborted, FinishedFail or Error in "
                "the last N hours (1-168), grouped per task with the latest error message."
            ),
            "parameters": {
                "type": "object",
                "properties": {"hours": {"type": "integer", "default": 24, "minimum": 1, "maximum": 168}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "live_upcoming_tasks",
            "description": "LIVE from Qlik: enabled tasks scheduled to run within the next N hours (1-48).",
            "parameters": {
                "type": "object",
                "properties": {"hours": {"type": "integer", "default": 6, "minimum": 1, "maximum": 48}},
            },
        },
    },
]

_TOOL_NAMES = frozenset(t["function"]["name"] for t in TOOL_SCHEMAS)

_LIVE_DISABLED = {
    "status": "unavailable",
    "message": "Live task status is disabled (QLIK_LIVE_TASKS_ENABLED=false).",
}


@dataclass
class AgentTools:
    """Real tools wired to the repository + Neo4j. Stub-friendly when deps are missing."""

    repository: PostgresRepository | None = None
    neo4j: Neo4jClient | None = None
    documentation: Any = None
    live_tasks: Any = None

    # -- search ---------------------------------------------------------------
    def search_apps(self, query: str) -> list[dict[str, Any]]:
        if self.repository is None:
            return [{"type": "App", "id": "SalesDashboard", "match": query}]
        return self._with_app_status(self.repository.search_nodes(query, type_filter="App"))

    def search_qvds(self, query: str) -> list[dict[str, Any]]:
        if self.repository is None:
            return [{"type": "QVD", "id": "Orders.qvd", "match": query}]
        return self.repository.search_nodes(query, type_filter="QVD")

    def search_tables(self, query: str) -> list[dict[str, Any]]:
        if self.repository is None:
            return [{"type": "Table", "id": "ORDERS", "match": query}]
        return self.repository.search_nodes(query, type_filter="Table")

    def qvd_variants(self, filename: str) -> list[dict[str, Any]]:
        if self.neo4j is None:
            return []
        return self.neo4j.qvd_variants(filename)

    def search_tasks(self, query: str) -> list[dict[str, Any]]:
        if self.repository is None:
            return [{"type": "Task", "id": "NightlyReload", "match": query}]
        return self.repository.search_nodes(query, type_filter="Task")

    # -- graph ----------------------------------------------------------------
    def upstream(self, node_type: str, node_id: str, depth: int = 5) -> list[dict[str, Any]]:
        if self.neo4j is None:
            return []
        return self.neo4j.upstream(node_type, node_id, depth)

    def downstream(self, node_type: str, node_id: str, depth: int = 5) -> list[dict[str, Any]]:
        if self.neo4j is None:
            return []
        return self.neo4j.downstream(node_type, node_id, depth)

    def impact(self, node_type: str, node_id: str, depth: int = 5) -> list[dict[str, Any]]:
        if self.neo4j is None:
            return []
        return self._with_app_status(self.neo4j.impact_scope(node_type, node_id, depth))

    def _with_app_status(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Tag App rows with their status so answers can separate live apps from noise."""
        if self.repository is None:
            return rows
        ids = [r.get("id") for r in rows if r.get("type") == "App"]
        try:
            statuses = self.repository.app_statuses(ids) if ids else {}
        except Exception:  # noqa: BLE001 - status is enrichment; never fail the tool
            logger.warning("App status lookup failed", exc_info=True)
            return rows
        for row in rows:
            status = statuses.get(row.get("id")) if row.get("type") == "App" else None
            if status:
                row["app_status"] = status.get("app_status")
                row["status_reason"] = status.get("status_reason")
        return rows

    def run_cypher(self, statement: str) -> list[dict[str, Any]]:
        if self.neo4j is None:
            return [{"statement": statement, "rows": []}]
        return self.neo4j.run_cypher(statement)

    # -- documentation --------------------------------------------------------
    def generate_app_documentation(self, app: str) -> dict[str, Any]:
        """Generate a documentation file and return a small receipt.

        The finished markdown is deliberately NOT returned. A tool result is
        echoed back into the conversation and re-sent on every subsequent turn,
        so returning a 1,500-token document would cost 1,500 tokens on every
        following message for the rest of the session. The frontend picks the
        receipt out of the trace and fetches the markdown once, over a separate
        request, when the user actually clicks download.
        """
        if self.documentation is None:
            return {"status": "unavailable",
                    "message": "Documentation generator is not configured."}
        # Imported here so the tools module stays importable when the docs
        # package or litellm is absent (the stub paths above rely on that).
        from app.docs.generator import AmbiguousApp, AppNotFound

        try:
            result = self.documentation.generate(app)
        except AmbiguousApp as exc:
            return {
                "status": "ambiguous",
                "message": str(exc),
                "candidates": [
                    {"app_id": c["app_id"], "name": c["name"]}
                    for c in exc.candidates[:10]
                ],
            }
        except AppNotFound as exc:
            return {"status": "not_found", "message": str(exc)}
        return {
            "status": "written",
            "app": result.app_name,
            "app_id": result.app_id,
            "filename": result.filename,
            "path": result.path,
            "bytes": result.bytes,
            "sections": result.sections,
            "evidence": result.evidence_label,
        }

    # -- live task state (read from QRS at question time, never stored) --------
    def live_task_status(self, task: str) -> dict[str, Any]:
        if self.live_tasks is None:
            return _LIVE_DISABLED
        return self.live_tasks.task_status(task)

    def live_running_tasks(self) -> dict[str, Any]:
        if self.live_tasks is None:
            return _LIVE_DISABLED
        return self.live_tasks.running_tasks()

    def live_task_failures(self, hours: int = 24) -> dict[str, Any]:
        if self.live_tasks is None:
            return _LIVE_DISABLED
        return self.live_tasks.task_failures(hours)

    def live_upcoming_tasks(self, hours: int = 6) -> dict[str, Any]:
        if self.live_tasks is None:
            return _LIVE_DISABLED
        return self.live_tasks.upcoming_tasks(hours)

    # -- dispatch -------------------------------------------------------------
    def dispatch(self, name: str, arguments: dict[str, Any]) -> Any:
        # Only advertised tools are callable: getattr alone would also expose
        # helpers and attributes (e.g. `live_tasks`) to whatever name the LLM emits.
        handler = getattr(self, name, None) if name in _TOOL_NAMES else None
        if handler is None or not callable(handler):
            raise ValueError(f"Unknown tool: {name}")
        return handler(**arguments)
