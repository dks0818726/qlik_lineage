from __future__ import annotations

import hashlib
import json
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - psycopg optional in unit tests
    psycopg = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]

from app.models.entities import GraphEdge, SCRIPT_DERIVED_RELATIONS

logger = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def script_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class PostgresRepository:
    """Synchronous repository over psycopg. Used by API, scanner, agent tools."""

    dsn: str

    def __post_init__(self) -> None:
        if psycopg is None:
            logger.warning("psycopg not installed; PostgresRepository runs in NO-OP mode.")

    # -- connection management ------------------------------------------------
    @contextmanager
    def _conn(self) -> Iterator[Any]:
        if psycopg is None:
            raise RuntimeError("psycopg is not installed; cannot open Postgres connection")
        with psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row) as conn:
            yield conn

    def init_schema(self) -> None:
        if psycopg is None:
            return
        sql = SCHEMA_FILE.read_text(encoding="utf-8")
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql)

    # -- apps -----------------------------------------------------------------
    def upsert_app(self, app_id: str, name: str, owner_id: str | None,
                   stream_id: str | None, script_hash_value: str | None,
                   modified_at: str | None = None) -> None:
        if psycopg is None:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO apps (app_id, name, owner_id, stream_id, script_hash, modified_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (app_id) DO UPDATE
                SET name = EXCLUDED.name,
                    owner_id = EXCLUDED.owner_id,
                    stream_id = EXCLUDED.stream_id,
                    script_hash = EXCLUDED.script_hash,
                    modified_at = EXCLUDED.modified_at,
                    updated_at = NOW();
                """,
                (app_id, name, owner_id, stream_id, script_hash_value, modified_at),
            )

    def list_apps(self, limit: int = 200) -> list[dict[str, Any]]:
        if psycopg is None:
            return []
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM apps ORDER BY updated_at DESC LIMIT %s", (limit,))
            return cur.fetchall()

    def get_app(self, app_id: str) -> dict[str, Any] | None:
        if psycopg is None:
            return None
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM apps WHERE app_id = %s", (app_id,))
            return cur.fetchone()

    # -- scripts --------------------------------------------------------------
    def upsert_script(self, app_id: str, script_text: str) -> str:
        if psycopg is None:
            return script_hash(script_text)
        h = script_hash(script_text)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scripts (app_id, script_text, script_hash)
                VALUES (%s, %s, %s)
                ON CONFLICT (app_id) DO UPDATE
                SET script_text = EXCLUDED.script_text,
                    script_hash = EXCLUDED.script_hash,
                    updated_at = NOW();
                """,
                (app_id, script_text, h),
            )
        return h

    def get_script(self, app_id: str) -> str | None:
        if psycopg is None:
            return None
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT script_text FROM scripts WHERE app_id = %s", (app_id,))
            row = cur.fetchone()
            return row["script_text"] if row else None

    # -- connections / qvds / tables / tasks ---------------------------------
    def upsert_connection(self, connection_id: str, name: str, ctype: str | None = None) -> None:
        if psycopg is None:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO connections (connection_id, name, connection_type)
                VALUES (%s, %s, %s)
                ON CONFLICT (connection_id) DO UPDATE
                SET name = EXCLUDED.name, connection_type = EXCLUDED.connection_type;
                """,
                (connection_id, name, ctype),
            )

    def upsert_qvd(self, qvd_path: str) -> None:
        if psycopg is None:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO qvds (qvd_path, name, last_seen) VALUES (%s, %s, NOW())
                ON CONFLICT (qvd_path) DO UPDATE SET last_seen = NOW();
                """,
                (qvd_path, qvd_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]),
            )

    def upsert_table(self, name: str, connection_id: str | None = None) -> None:
        if psycopg is None:
            return
        table_id = f"{connection_id}.{name}" if connection_id else name
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO source_tables (table_id, name, connection_id, last_seen)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (table_id) DO UPDATE SET last_seen = NOW();
                """,
                (table_id, name, connection_id),
            )

    def upsert_task(self, task_id: str, name: str, app_id: str | None,
                    schedule_id: str | None = None,
                    depends_on_task_id: str | None = None) -> None:
        if psycopg is None:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tasks (task_id, name, app_id, schedule_id, depends_on_task_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (task_id) DO UPDATE
                SET name = EXCLUDED.name,
                    app_id = EXCLUDED.app_id,
                    schedule_id = EXCLUDED.schedule_id,
                    depends_on_task_id = EXCLUDED.depends_on_task_id;
                """,
                (task_id, name, app_id, schedule_id, depends_on_task_id),
            )

    def upsert_schedule(self, schedule_id: str, name: str, cron: str | None = None) -> None:
        if psycopg is None:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO schedules (schedule_id, name, cron_expression)
                VALUES (%s, %s, %s)
                ON CONFLICT (schedule_id) DO UPDATE
                SET name = EXCLUDED.name, cron_expression = EXCLUDED.cron_expression;
                """,
                (schedule_id, name, cron),
            )

    def upsert_owner(self, owner_id: str, name: str, email: str | None = None) -> None:
        if psycopg is None:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO owners (owner_id, name, email) VALUES (%s, %s, %s)
                ON CONFLICT (owner_id) DO UPDATE SET name = EXCLUDED.name, email = EXCLUDED.email;
                """,
                (owner_id, name, email),
            )

    def upsert_stream(self, stream_id: str, name: str) -> None:
        if psycopg is None:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO streams (stream_id, name) VALUES (%s, %s)
                ON CONFLICT (stream_id) DO UPDATE SET name = EXCLUDED.name;
                """,
                (stream_id, name),
            )

    # -- edges ---------------------------------------------------------------
    def upsert_edges(self, edges: Iterable[GraphEdge]) -> int:
        if psycopg is None:
            return 0
        count = 0
        with self._conn() as conn, conn.cursor() as cur:
            for edge in edges:
                cur.execute(
                    """
                    INSERT INTO lineage_edges (source_type, source_id, relation, target_type, target_id, attributes)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (source_type, source_id, relation, target_type, target_id) DO UPDATE
                    SET attributes = lineage_edges.attributes || EXCLUDED.attributes,
                        updated_at = NOW();
                    """,
                    (
                        edge.source_type,
                        edge.source_id,
                        edge.relation,
                        edge.target_type,
                        edge.target_id,
                        json.dumps(edge.attributes or {}),
                    ),
                )
                count += 1
        return count

    def delete_app_edges(self, app_id: str, relations: Iterable[str] | None = None) -> int:
        """Remove the lineage edges this app is an endpoint of.

        Edges are only ever upserted, so re-scanning a changed script adds the new
        dependencies but leaves the old ones behind. Clearing an app's edges before
        re-inserting keeps lineage in step with the current script instead of
        accumulating every dependency the app has *ever* had.

        ``relations`` defaults to the script-derived relations so that QRS-sourced
        metadata (OWNS/RUNS/BELONGS_TO) is preserved. Pass ``()`` to clear every
        edge, which is what deleting an app outright needs.
        """
        if psycopg is None:
            return 0
        rels = tuple(SCRIPT_DERIVED_RELATIONS if relations is None else relations)
        clause = "((source_type = 'App' AND source_id = %s) OR (target_type = 'App' AND target_id = %s))"
        params: list[Any] = [app_id, app_id]
        if rels:
            clause += " AND relation = ANY(%s)"
            params.append(list(rels))
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM lineage_edges WHERE {clause};", params)
            return cur.rowcount or 0

    def delete_app(self, app_id: str) -> None:
        """Remove an app that no longer exists in Qlik, along with its edges."""
        if psycopg is None:
            return
        self.delete_app_edges(app_id, relations=())
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM scripts WHERE app_id = %s;", (app_id,))
            cur.execute("UPDATE tasks SET app_id = NULL WHERE app_id = %s;", (app_id,))
            cur.execute("DELETE FROM apps WHERE app_id = %s;", (app_id,))

    def prune_orphan_nodes(self) -> dict[str, int]:
        """Drop QVD/table rows no longer referenced by any edge.

        Deleting apps can leave QVDs and source tables with no remaining lineage.
        """
        if psycopg is None:
            return {}
        removed: dict[str, int] = {}
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM qvds q WHERE NOT EXISTS (
                    SELECT 1 FROM lineage_edges e
                    WHERE (e.source_type = 'QVD' AND e.source_id = q.qvd_path)
                       OR (e.target_type = 'QVD' AND e.target_id = q.qvd_path)
                );
                """
            )
            removed["qvds"] = cur.rowcount or 0
            cur.execute(
                """
                DELETE FROM source_tables t WHERE NOT EXISTS (
                    SELECT 1 FROM lineage_edges e
                    WHERE (e.source_type = 'Table' AND (e.source_id = t.name
                                                        OR e.source_id LIKE '%.' || t.name))
                       OR (e.target_type = 'Table' AND (e.target_id = t.name
                                                        OR e.target_id LIKE '%.' || t.name))
                );
                """
            )
            removed["tables"] = cur.rowcount or 0
        return removed

    # -- search & traversal --------------------------------------------------
    def search_nodes(self, query: str, type_filter: str | None = None,
                     limit: int = 25) -> list[dict[str, Any]]:
        if psycopg is None:
            return []
        like = f"%{query.lower()}%"
        results: list[dict[str, Any]] = []
        with self._conn() as conn, conn.cursor() as cur:
            if not type_filter or type_filter == "App":
                cur.execute(
                    "SELECT 'App' AS type, app_id AS id, name FROM apps "
                    "WHERE LOWER(name) LIKE %s OR LOWER(app_id) LIKE %s LIMIT %s",
                    (like, like, limit),
                )
                results.extend(cur.fetchall())
            if not type_filter or type_filter == "QVD":
                cur.execute(
                    "SELECT 'QVD' AS type, qvd_path AS id, name FROM qvds "
                    "WHERE LOWER(qvd_path) LIKE %s LIMIT %s",
                    (like, limit),
                )
                results.extend(cur.fetchall())
            if not type_filter or type_filter == "Table":
                cur.execute(
                    "SELECT 'Table' AS type, table_id AS id, name FROM source_tables "
                    "WHERE LOWER(table_id) LIKE %s LIMIT %s",
                    (like, limit),
                )
                results.extend(cur.fetchall())
            if not type_filter or type_filter == "Task":
                cur.execute(
                    "SELECT 'Task' AS type, task_id AS id, name FROM tasks "
                    "WHERE LOWER(name) LIKE %s OR LOWER(task_id) LIKE %s LIMIT %s",
                    (like, like, limit),
                )
                results.extend(cur.fetchall())
        return results

    # -- scan runs / change log ----------------------------------------------
    def start_scan(self, mode: str) -> str:
        scan_id = str(uuid.uuid4())
        if psycopg is None:
            return scan_id
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO scan_runs (scan_id, mode, status) VALUES (%s, %s, 'running')",
                (scan_id, mode),
            )
        return scan_id

    def finish_scan(self, scan_id: str, status: str, stats: dict[str, Any]) -> None:
        if psycopg is None:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE scan_runs SET status = %s, completed_at = NOW(), stats = %s::jsonb "
                "WHERE scan_id = %s",
                (status, json.dumps(stats), scan_id),
            )

    def log_change(self, scan_id: str, object_type: str, object_id: str, change_type: str) -> None:
        if psycopg is None:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO change_log (scan_id, object_type, object_id, change_type) "
                "VALUES (%s, %s, %s, %s)",
                (scan_id, object_type, object_id, change_type),
            )
