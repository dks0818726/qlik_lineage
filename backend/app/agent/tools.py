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
]


@dataclass
class AgentTools:
    """Real tools wired to the repository + Neo4j. Stub-friendly when deps are missing."""

    repository: PostgresRepository | None = None
    neo4j: Neo4jClient | None = None

    # -- search ---------------------------------------------------------------
    def search_apps(self, query: str) -> list[dict[str, Any]]:
        if self.repository is None:
            return [{"type": "App", "id": "SalesDashboard", "match": query}]
        return self.repository.search_nodes(query, type_filter="App")

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
        return self.neo4j.impact(node_type, node_id, depth)

    def run_cypher(self, statement: str) -> list[dict[str, Any]]:
        if self.neo4j is None:
            return [{"statement": statement, "rows": []}]
        return self.neo4j.run_cypher(statement)

    # -- dispatch -------------------------------------------------------------
    def dispatch(self, name: str, arguments: dict[str, Any]) -> Any:
        handler = getattr(self, name, None)
        if handler is None or not callable(handler):
            raise ValueError(f"Unknown tool: {name}")
        return handler(**arguments)
