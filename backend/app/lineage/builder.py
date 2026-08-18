from __future__ import annotations

from typing import Iterable

from app.models.entities import GraphEdge, ParsedDependency, QlikApp, QlikTask


class LineageBuilder:
    """Build directed lineage edges from parsed script deps and QRS metadata."""

    def build_edges(self, dependencies: list[ParsedDependency]) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        for dep in dependencies:
            app_key = dep.app_id

            if dep.source_table:
                table_id = (
                    f"{dep.connection}.{dep.source_table}" if dep.connection else dep.source_table
                )
                edges.append(GraphEdge("Table", table_id, "READS", "App", app_key))
                if dep.connection:
                    edges.append(
                        GraphEdge("Table", table_id, "BELONGS_TO", "Connection", dep.connection)
                    )

            if dep.output_qvd:
                edges.append(GraphEdge("App", app_key, "WRITES", "QVD", dep.output_qvd))

            if dep.input_qvd:
                edges.append(GraphEdge("QVD", dep.input_qvd, "READS", "App", app_key))

            if dep.connection:
                edges.append(GraphEdge("App", app_key, "USES", "Connection", dep.connection))

            if dep.resident_table:
                edges.append(
                    GraphEdge("App", app_key, "DEPENDS_ON", "Table", dep.resident_table)
                )

            if dep.include_file:
                edges.append(
                    GraphEdge("App", app_key, "USES", "Connection", dep.include_file)
                )

        return self._dedupe(edges)

    def build_app_metadata_edges(self, apps: Iterable[QlikApp]) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        for app in apps:
            if app.owner_id:
                edges.append(GraphEdge("Owner", app.owner_id, "OWNS", "App", app.app_id))
            if app.stream_id:
                edges.append(GraphEdge("App", app.app_id, "BELONGS_TO", "Stream", app.stream_id))
        return self._dedupe(edges)

    def build_task_edges(self, tasks: Iterable[QlikTask]) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        for task in tasks:
            if task.app_id:
                edges.append(GraphEdge("Task", task.task_id, "RUNS", "App", task.app_id))
            if task.schedule_id:
                edges.append(
                    GraphEdge("Task", task.task_id, "SCHEDULED_BY", "Schedule", task.schedule_id)
                )
            if task.depends_on_task_id:
                edges.append(
                    GraphEdge("Task", task.task_id, "DEPENDS_ON", "Task", task.depends_on_task_id)
                )
        return self._dedupe(edges)

    def _dedupe(self, edges: list[GraphEdge]) -> list[GraphEdge]:
        seen: set[tuple[str, str, str, str, str]] = set()
        unique: list[GraphEdge] = []
        for edge in edges:
            token = (
                edge.source_type,
                edge.source_id.lower(),
                edge.relation,
                edge.target_type,
                edge.target_id.lower(),
            )
            if token not in seen:
                seen.add(token)
                unique.append(edge)
        return unique
