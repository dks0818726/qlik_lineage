from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from app.connectors.engine_client import EngineClient
from app.connectors.qrs_client import QrsClient
from app.graph.neo4j_client import Neo4jClient
from app.lineage.builder import LineageBuilder
from app.models.entities import GraphEdge, QlikApp, QlikTask, ScanObject
from app.parser.qlik_parser import QlikScriptParser
from app.realtime.events import event_bus, lineage_event
from app.scanner.change_detector import detect_changes
from app.storage.repository import PostgresRepository, script_hash

logger = logging.getLogger(__name__)


@dataclass
class ScannerOrchestrator:
    """Pulls metadata from QRS + Engine, parses scripts, builds lineage,
    upserts PostgreSQL and Neo4j, and emits realtime events.

    Modes:
      * full  – reload everything regardless of hashes
      * delta – only scan apps whose ``modifiedDate`` or script hash changed
    """

    qrs: QrsClient
    engine: EngineClient
    repository: PostgresRepository
    neo4j: Neo4jClient
    parser: QlikScriptParser
    builder: LineageBuilder
    max_concurrent_apps: int = 1

    def run(self, mode: str = "full") -> dict[str, Any]:
        if mode not in {"full", "delta"}:
            raise ValueError("mode must be 'full' or 'delta'")
        scan_id = self.repository.start_scan(mode)
        event_bus.publish_sync(lineage_event("scan.started", {"scan_id": scan_id, "mode": mode}))

        stats: dict[str, int] = {
            "apps_seen": 0,
            "apps_changed": 0,
            "edges_upserted": 0,
            "new_qvds": 0,
            "deleted_apps": 0,
        }
        try:
            apps = self.qrs.fetch_apps()
            # Auxiliary lookups are enrichment only. A slow, hanging, or
            # unavailable QRS endpoint (e.g. a very large /qrs/user/full dump)
            # must not abort the whole scan, so fetch each defensively.
            tasks = self._safe_fetch("reloadtask", self.qrs.fetch_reload_tasks)
            connections = self._safe_fetch("dataconnection", self.qrs.fetch_data_connections)
            owners = self._safe_fetch("user", self.qrs.fetch_owners)
            streams = self._safe_fetch("stream", self.qrs.fetch_streams)

            self._sync_lookups(connections, owners, streams)

            previous = self._previous_scan_objects()
            current = [
                ScanObject(object_id=a.get("id") or a.get("app_id"),
                           hash_value=a.get("modifiedDate") or a.get("modified_at") or "",
                           object_type="App")
                for a in apps
            ]
            diff = detect_changes(previous, current)
            deleted_objects = diff["deleted"]

            # Safety valve: deletions are now destructive, and a partial QRS response
            # (timeout, paging glitch) would otherwise look like a mass deletion. If more
            # than a fifth of known apps vanish at once, treat it as a bad fetch and skip
            # deletions rather than wiping real lineage. A genuine bulk delete is picked
            # up on the next scan once the app list is stable.
            if previous and len(deleted_objects) > max(10, int(0.2 * len(previous))):
                logger.error(
                    "Refusing to delete %d of %d apps in one scan - the Qlik app list "
                    "looks incomplete. Skipping deletions for scan %s.",
                    len(deleted_objects), len(previous), scan_id,
                )
                stats["deletions_skipped"] = len(deleted_objects)
                deleted_objects = []

            stats["deleted_apps"] = len(deleted_objects)

            for deleted in deleted_objects:
                # Remove the app outright: previously deletions were only logged, so an
                # app deleted in Qlik stayed in the graph and kept appearing in answers.
                try:
                    self.repository.delete_app(deleted.object_id)
                    self.neo4j.delete_app(deleted.object_id)
                except Exception:  # noqa: BLE001 - never abort a scan over one deletion
                    logger.exception("Failed to delete app %s", deleted.object_id)
                self.repository.log_change(scan_id, "App", deleted.object_id, "deleted")
                event_bus.publish_sync(
                    lineage_event("node.deleted", {"type": "App", "id": deleted.object_id})
                )

            target_apps = apps if mode == "full" else [
                app for app in apps
                if any(c.object_id == (app.get("id") or app.get("app_id")) for c in diff["created"] + diff["updated"])
            ]

            for app in target_apps:
                stats["apps_seen"] += 1

            def _run_app(app: dict[str, Any]) -> tuple[int, int, str | None]:
                app_id = app.get("id") or app.get("app_id")
                try:
                    edges_count, qvd_count = self._process_app(scan_id, app)
                    return edges_count, qvd_count, None
                except Exception as exc:  # continue on failure per prompt requirement
                    logger.exception("App %s scan failed", app_id)
                    return 0, 0, str(exc)

            workers = max(1, int(self.max_concurrent_apps or 1))
            if workers == 1 or len(target_apps) <= 1:
                results = [_run_app(app) for app in target_apps]
            else:
                logger.info("Scanning %d apps with %d workers", len(target_apps), workers)
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {pool.submit(_run_app, app): app for app in target_apps}
                    results = [f.result() for f in as_completed(futures)]

            for edges_count, qvd_count, err in results:
                if err:
                    continue
                if edges_count:
                    stats["apps_changed"] += 1
                stats["edges_upserted"] += edges_count
                stats["new_qvds"] += qvd_count

            task_edges = self._process_tasks(tasks)
            stats["edges_upserted"] += self.repository.upsert_edges(task_edges)
            self.neo4j.upsert_edges(task_edges)

            # Deleting apps and pruning stale edges can strand QVDs/tables with no
            # remaining lineage; drop them so counts reflect what is actually in use.
            try:
                pruned = self.repository.prune_orphan_nodes()
                stats["pruned_qvds"] = pruned.get("qvds", 0)
                stats["pruned_tables"] = pruned.get("tables", 0)
                stats["pruned_graph_nodes"] = self.neo4j.prune_orphan_nodes()
            except Exception:  # noqa: BLE001 - pruning is housekeeping, never fatal
                logger.exception("Orphan pruning failed; scan results are still valid")

            self.repository.finish_scan(scan_id, "succeeded", stats)
            event_bus.publish_sync(
                lineage_event("scan.finished", {"scan_id": scan_id, "stats": stats})
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Scan %s failed", scan_id)
            self.repository.finish_scan(scan_id, "failed", {"error": str(exc)})
            event_bus.publish_sync(
                lineage_event("scan.failed", {"scan_id": scan_id, "error": str(exc)})
            )
            raise

        return {"scan_id": scan_id, "stats": stats}

    # -- helpers -------------------------------------------------------------
    def _safe_fetch(self, label: str, fetch_fn) -> list[dict[str, Any]]:
        try:
            return fetch_fn()
        except Exception as exc:  # noqa: BLE001 - continue scan without this lookup
            logger.warning("QRS %s fetch failed (%s); continuing without it", label, exc)
            return []

    def _sync_lookups(self, connections: list[dict[str, Any]],
                      owners: list[dict[str, Any]], streams: list[dict[str, Any]]) -> None:
        for c in connections:
            cid = c.get("id") or c.get("connection_id") or c.get("name")
            self.repository.upsert_connection(cid, c.get("name", cid), c.get("type"))
        for o in owners:
            oid = o.get("id") or o.get("user_id")
            name = o.get("name") or f"{o.get('userDirectory','')}\\{o.get('userId','')}"
            self.repository.upsert_owner(oid, name, o.get("email"))
        for s in streams:
            sid = s.get("id") or s.get("stream_id")
            self.repository.upsert_stream(sid, s.get("name", sid))

    def _previous_scan_objects(self) -> list[ScanObject]:
        return [
            ScanObject(
                object_id=row["app_id"],
                hash_value=str(row.get("modified_at") or row.get("script_hash") or ""),
                object_type="App",
            )
            for row in self.repository.list_apps(limit=10000)
        ]

    def _process_app(self, scan_id: str, app: dict[str, Any]) -> tuple[int, int]:
        app_id = app.get("id") or app.get("app_id")
        name = app.get("name") or app_id
        owner_id = app.get("owner", {}).get("id") if isinstance(app.get("owner"), dict) else app.get("owner_id")
        stream_id = app.get("stream", {}).get("id") if isinstance(app.get("stream"), dict) else app.get("stream_id")
        modified = app.get("modifiedDate") or app.get("modified_at")

        try:
            script = self.engine.get_load_script(app_id)
        except Exception as exc:
            logger.warning("Failed to fetch script for app %s: %s", app_id, exc)
            return 0, 0

        new_hash = script_hash(script)
        existing = self.repository.get_app(app_id)
        is_new = existing is None
        changed = is_new or existing.get("script_hash") != new_hash

        self.repository.upsert_app(app_id, name, owner_id, stream_id, new_hash, modified)
        self.repository.upsert_script(app_id, script)

        if not changed:
            return 0, 0

        self.repository.log_change(scan_id, "App", app_id, "created" if is_new else "updated")
        event_bus.publish_sync(
            lineage_event("node.updated" if not is_new else "node.created",
                          {"type": "App", "id": app_id, "name": name})
        )

        deps = self.parser.parse(app_id=app_id, script=script)
        qvd_count = 0

        # The script changed, so dependencies it no longer declares must go. Edges are
        # upsert-only, so without this an app keeps every QVD/table it has ever read.
        self.repository.delete_app_edges(app_id)
        self.neo4j.delete_app_edges(app_id)

        for dep in deps:
            if dep.connection:
                self.repository.upsert_connection(dep.connection, dep.connection)
            if dep.source_table:
                self.repository.upsert_table(dep.source_table, dep.connection)
            if dep.input_qvd:
                self.repository.upsert_qvd(dep.input_qvd)
            if dep.output_qvd:
                self.repository.upsert_qvd(dep.output_qvd)
                qvd_count += 1

        edges = self.builder.build_edges(deps)
        edges += self.builder.build_app_metadata_edges([
            QlikApp(app_id=app_id, name=name, owner_id=owner_id, stream_id=stream_id)
        ])
        self.repository.upsert_edges(edges)
        self.neo4j.upsert_edges(edges)
        for edge in edges:
            event_bus.publish_sync(
                lineage_event("edge.upserted", {
                    "source": {"type": edge.source_type, "id": edge.source_id},
                    "target": {"type": edge.target_type, "id": edge.target_id},
                    "relation": edge.relation,
                })
            )
        return len(edges), qvd_count

    def _process_tasks(self, tasks: list[dict[str, Any]]) -> list[GraphEdge]:
        qlik_tasks: list[QlikTask] = []
        for t in tasks:
            tid = t.get("id") or t.get("task_id")
            name = t.get("name", tid)
            app = t.get("app", {}) if isinstance(t.get("app"), dict) else {}
            app_id = app.get("id") or t.get("app_id")
            schedule_id = (
                t.get("schedule", {}).get("id") if isinstance(t.get("schedule"), dict)
                else t.get("schedule_id")
            )
            depends_on = (
                t.get("dependsOn", {}).get("id") if isinstance(t.get("dependsOn"), dict)
                else t.get("depends_on_task_id")
            )
            self.repository.upsert_task(tid, name, app_id, schedule_id, depends_on)
            qlik_tasks.append(QlikTask(task_id=tid, name=name, app_id=app_id,
                                       schedule_id=schedule_id, depends_on_task_id=depends_on))
        return self.builder.build_task_edges(qlik_tasks)
