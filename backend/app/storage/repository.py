from __future__ import annotations

import hashlib
import json
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Iterable, Iterator

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

    # -- display names --------------------------------------------------------
    # Graph nodes carry only an `id`, so anything showing a graph to a human has to
    # resolve those ids back to names here. Apps are the important case: an app id is
    # an opaque GUID and is meaningless on screen.
    _NAME_SOURCES: ClassVar[dict[str, tuple[str, str]]] = {
        "App": ("apps", "app_id"),
        "Task": ("tasks", "task_id"),
        "Owner": ("owners", "owner_id"),
        "Stream": ("streams", "stream_id"),
        "Schedule": ("schedules", "schedule_id"),
        "Connection": ("connections", "connection_id"),
        "QVD": ("qvds", "qvd_path"),
        "Table": ("source_tables", "table_id"),
    }

    def display_names(self, ids_by_type: dict[str, list[str]]) -> dict[str, str]:
        """Map ``"<Type>::<id>"`` to a human-readable name for the given ids.

        Types without a name table, and ids with no matching row, are simply absent
        from the result so callers can fall back to the raw id.
        """
        names: dict[str, str] = {}
        if psycopg is None:
            return names
        with self._conn() as conn, conn.cursor() as cur:
            for node_type, ids in ids_by_type.items():
                source = self._NAME_SOURCES.get(node_type)
                if not source or not ids:
                    continue
                table, key_column = source
                cur.execute(
                    f"SELECT {key_column} AS key, name FROM {table} WHERE {key_column} = ANY(%s)",
                    (list(ids),),
                )
                for row in cur.fetchall():
                    if row["name"]:
                        names[f"{node_type}::{row['key']}"] = row["name"]
        return names

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

    # -- generated documentation ---------------------------------------------
    def upsert_documentation(self, app_id: str, markdown: str, filename: str,
                             script_hash_value: str | None, model: str | None,
                             evidence_level: int, sections: int) -> None:
        if psycopg is None:
            return
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_documentation
                    (app_id, markdown, filename, script_hash, model,
                     evidence_level, sections, generated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (app_id) DO UPDATE
                SET markdown = EXCLUDED.markdown,
                    filename = EXCLUDED.filename,
                    script_hash = EXCLUDED.script_hash,
                    model = EXCLUDED.model,
                    evidence_level = EXCLUDED.evidence_level,
                    sections = EXCLUDED.sections,
                    generated_at = NOW();
                """,
                (app_id, markdown, filename, script_hash_value, model,
                 evidence_level, sections),
            )

    def get_documentation(self, app_id: str) -> dict[str, Any] | None:
        """Stored document plus a staleness flag.

        `stale` compares the script hash recorded at generation time with the
        app's current hash, so a document written before a script change is
        reported as out of date rather than presented as current.
        """
        if psycopg is None:
            return None
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.*, a.name AS app_name, a.script_hash AS current_script_hash
                FROM app_documentation d
                JOIN apps a ON a.app_id = d.app_id
                WHERE d.app_id = %s
                """,
                (app_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            row["stale"] = bool(
                row["script_hash"]
                and row["current_script_hash"]
                and row["script_hash"] != row["current_script_hash"]
            )
            return row

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
        """Fuzzy node lookup across every entity type shown in the UI.

        Ranking matters here: a substring match on a long QVD path or a
        connection-prefixed table id would otherwise bury the exact object the
        user typed. Results are ordered exact id > exact name > name prefix >
        basename match > anything else, so the intended node is first.
        """
        if psycopg is None:
            return []
        q = query.strip().lower()
        like = f"%{q}%"
        results: list[dict[str, Any]] = []

        # rank 0 = exact id, 1 = exact name, 2 = name starts with, 3 = ends with
        # (basename of a path), 4 = plain substring.
        def ranked(id_col: str, name_col: str) -> str:
            return (
                f"CASE WHEN LOWER({id_col}) = %(q)s THEN 0 "
                f"WHEN LOWER({name_col}) = %(q)s THEN 1 "
                f"WHEN LOWER({name_col}) LIKE %(prefix)s THEN 2 "
                f"WHEN LOWER({id_col}) LIKE %(suffix)s THEN 3 "
                "ELSE 4 END AS rank"
            )

        args = {"q": q, "like": like, "prefix": f"{q}%", "suffix": f"%{q}", "limit": limit}

        # (type, table, id column, name column)
        sources: list[tuple[str, str, str, str]] = [
            ("App", "apps", "app_id", "name"),
            ("QVD", "qvds", "qvd_path", "COALESCE(name, qvd_path)"),
            ("Table", "source_tables", "table_id", "name"),
            ("Task", "tasks", "task_id", "name"),
            ("Connection", "connections", "connection_id", "name"),
            ("Owner", "owners", "owner_id", "name"),
            ("Stream", "streams", "stream_id", "name"),
        ]

        with self._conn() as conn, conn.cursor() as cur:
            for node_type, table, id_col, name_col in sources:
                if type_filter and type_filter != node_type:
                    continue
                cur.execute(
                    f"SELECT '{node_type}' AS type, {id_col} AS id, {name_col} AS name, "
                    f"{ranked(id_col, name_col)} "
                    f"FROM {table} "
                    f"WHERE LOWER({id_col}) LIKE %(like)s OR LOWER({name_col}) LIKE %(like)s "
                    f"ORDER BY rank, {id_col} LIMIT %(limit)s",
                    args,
                )
                results.extend(cur.fetchall())

        results.sort(key=lambda r: (r.get("rank", 4), len(r.get("id") or "")))
        for r in results:
            r.pop("rank", None)
        # Each type is already capped at `limit` by its own query. Only trim the
        # combined list when the caller asked for a single type, so an untyped
        # search keeps the same recall it had before ranking was introduced.
        return results[:limit] if type_filter else results

    def resolve_node(self, node_type: str, reference: str) -> dict[str, Any] | None:
        """Return the single node a reference points at, or None if it is not unique.

        Impact analysis previously required the caller to know the exact graph id -
        an app GUID, a full `lib://...` QVD path, or a connection-prefixed table id -
        and silently returned an empty result when that guess was wrong. Callers use
        this to accept a human-typed name and fall back to showing candidates.
        """
        if psycopg is None:
            return None
        matches = self.search_nodes(reference, type_filter=node_type, limit=25)
        if not matches:
            return None
        ref = reference.strip().lower()
        for m in matches:
            if (m["id"] or "").lower() == ref:
                return m
        if len(matches) == 1:
            return matches[0]
        exact_names = [m for m in matches if (m.get("name") or "").lower() == ref]
        if len(exact_names) == 1:
            return exact_names[0]
        return None

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
