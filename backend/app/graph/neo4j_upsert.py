from __future__ import annotations

from collections.abc import Iterable

from app.models.entities import GraphEdge


NODE_TYPES = ("App", "QVD", "Table", "Connection", "Task", "Owner", "Stream", "Schedule")
REL_TYPES = ("READS", "WRITES", "USES", "RUNS", "DEPENDS_ON", "OWNS", "BELONGS_TO", "SCHEDULED_BY")


def constraint_queries() -> list[str]:
    return [
        f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        for label in NODE_TYPES
    ]


def upsert_edge_query(edge: GraphEdge) -> tuple[str, dict[str, object]]:
    if edge.relation not in REL_TYPES:
        raise ValueError(f"Unsupported relationship type: {edge.relation}")

    query = f"""
    MERGE (s:{edge.source_type} {{id: $source_id}})
    MERGE (t:{edge.target_type} {{id: $target_id}})
    MERGE (s)-[r:{edge.relation}]->(t)
    SET r += $attrs
    """
    params: dict[str, object] = {
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "attrs": edge.attributes,
    }
    return query, params


def bulk_upsert_payload(edges: Iterable[GraphEdge]) -> list[tuple[str, dict[str, object]]]:
    return [upsert_edge_query(edge) for edge in edges]
