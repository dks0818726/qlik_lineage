from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

try:
    from neo4j import GraphDatabase, Driver
except ImportError:  # pragma: no cover - neo4j optional at test time
    GraphDatabase = None  # type: ignore[assignment]
    Driver = None  # type: ignore[assignment]

from app.graph.neo4j_upsert import bulk_upsert_payload, constraint_queries
from app.models.entities import GraphEdge, SCRIPT_DERIVED_RELATIONS

logger = logging.getLogger(__name__)

# Cypher read-only guard: only allow MATCH/RETURN/WITH/UNWIND/CALL/WHERE/ORDER/LIMIT/SKIP/USE.
WRITE_KEYWORDS = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DETACH|DROP|FOREACH|CALL\s+apoc\.(?!coll|text|convert))\b",
    re.IGNORECASE,
)


def assert_read_only(cypher: str) -> None:
    if WRITE_KEYWORDS.search(cypher):
        raise PermissionError("Cypher statement contains write/destructive keywords; read-only mode enforced.")


@dataclass
class Neo4jClient:
    uri: str
    user: str
    password: str

    def __post_init__(self) -> None:
        self._driver: Driver | None = None
        if GraphDatabase is None:
            logger.warning("neo4j driver not installed; Neo4jClient runs in NO-OP mode.")

    # -- lifecycle ------------------------------------------------------------
    def connect(self) -> None:
        if self._driver is not None or GraphDatabase is None:
            return
        self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    # -- schema ---------------------------------------------------------------
    def init_schema(self) -> None:
        if self._driver is None:
            self.connect()
        if self._driver is None:
            return
        with self._driver.session() as session:
            for stmt in constraint_queries():
                session.run(stmt)

    # -- writes ---------------------------------------------------------------
    def upsert_edges(self, edges: Iterable[GraphEdge]) -> int:
        if self._driver is None:
            self.connect()
        if self._driver is None:
            return 0
        payload = bulk_upsert_payload(edges)
        with self._driver.session() as session:
            for query, params in payload:
                session.run(query, **params)
        return len(payload)

    # -- reads ----------------------------------------------------------------
    # Agent tool calls feed these chains straight into an LLM prompt, and 500 full
    # path chains routinely exceeded the model context window (one failure measured
    # at 185k tokens against a 64k limit), so agent-facing calls default to a bounded
    # number of paths. UI-facing callers (e.g. /graph/impact-report) pass a much
    # larger max_paths since they render the full list rather than an LLM prompt.
    _MAX_PATHS = 60
    _MAX_NODE_SAMPLE = 200

    def upstream(self, node_type: str, node_id: str, depth: int = 5, max_paths: int | None = None) -> list[dict[str, Any]]:
        depth = max(1, min(depth, 25))
        limit = self._MAX_PATHS if max_paths is None else max_paths
        cypher = (
            f"MATCH path = (start:{node_type} {{id: $id}})<-[*1..{depth}]-(n) "
            "WITH n, path, length(path) AS d ORDER BY d ASC "
            "WITH n, collect(path)[0] AS path, min(d) AS depth "
            "RETURN [x IN nodes(path) | {type: head(labels(x)), id: x.id}] AS chain, depth "
            f"ORDER BY depth ASC LIMIT {limit}"
        )
        return self._read(cypher, {"id": node_id})

    def downstream(self, node_type: str, node_id: str, depth: int = 5, max_paths: int | None = None) -> list[dict[str, Any]]:
        depth = max(1, min(depth, 25))
        limit = self._MAX_PATHS if max_paths is None else max_paths
        cypher = (
            f"MATCH path = (start:{node_type} {{id: $id}})-[*1..{depth}]->(n) "
            "WITH n, path, length(path) AS d ORDER BY d ASC "
            "WITH n, collect(path)[0] AS path, min(d) AS depth "
            "RETURN [x IN nodes(path) | {type: head(labels(x)), id: x.id}] AS chain, depth "
            f"ORDER BY depth ASC LIMIT {limit}"
        )
        return self._read(cypher, {"id": node_id})

    def impact(self, node_type: str, node_id: str, depth: int = 5, node_limit: int | None = None) -> list[dict[str, Any]]:
        """Distinct downstream nodes, plus a per-type count so totals survive truncation."""
        depth = max(1, min(depth, 25))
        limit = self._MAX_NODE_SAMPLE if node_limit is None else node_limit
        counts = self._read(
            f"MATCH (start:{node_type} {{id: $id}})-[*1..{depth}]->(n) "
            "RETURN head(labels(n)) AS type, count(DISTINCT n) AS total "
            "ORDER BY total DESC",
            {"id": node_id},
        )
        nodes = self._read(
            f"MATCH (start:{node_type} {{id: $id}})-[*1..{depth}]->(n) "
            f"RETURN DISTINCT head(labels(n)) AS type, n.id AS id LIMIT {limit}",
            {"id": node_id},
        )
        return [{"_totals_by_type": counts, "_sample_size": len(nodes)}, *nodes]

    def impact_scope(self, node_type: str, node_id: str, depth: int = 5, node_limit: int | None = None) -> list[dict[str, Any]]:
        """Impact for visualization, with the traversal direction chosen per node type.

        `impact()` walks outgoing edges only. That is correct for Apps, QVDs, Tables
        and Tasks, which point at their consumers, but a Connection is only ever an
        edge *target* - the graph stores `(App)-[:USES]->(Connection)` and
        `(Table)-[:BELONGS_TO]->(Connection)`. Walking outgoing from a Connection
        therefore always found nothing, so connection impact silently came back empty
        even when hundreds of apps used it.

        For those inbound-only types the first hop is reversed to collect the direct
        dependants, then normal downstream traversal continues from each of them.
        """
        depth = max(1, min(depth, 25))
        limit = self._MAX_NODE_SAMPLE if node_limit is None else node_limit
        inbound_first = node_type in {"Connection", "Owner", "Stream", "Schedule"}

        if not inbound_first:
            return self.impact(node_type, node_id, depth, node_limit=limit)

        remaining = max(0, depth - 1)
        counts = self._read(
            f"MATCH (c:{node_type} {{id: $id}})<-[]-(d) "
            f"OPTIONAL MATCH (d)-[*0..{remaining}]->(x) "
            "WITH collect(DISTINCT d) + collect(DISTINCT x) AS all_nodes "
            "UNWIND all_nodes AS n "
            "WITH n WHERE n IS NOT NULL "
            "RETURN head(labels(n)) AS type, count(DISTINCT n) AS total "
            "ORDER BY total DESC",
            {"id": node_id},
        )
        nodes = self._read(
            f"MATCH (c:{node_type} {{id: $id}})<-[]-(d) "
            f"OPTIONAL MATCH (d)-[*0..{remaining}]->(x) "
            "WITH collect(DISTINCT d) + collect(DISTINCT x) AS all_nodes "
            "UNWIND all_nodes AS n "
            "WITH DISTINCT n WHERE n IS NOT NULL "
            f"RETURN head(labels(n)) AS type, n.id AS id LIMIT {limit}",
            {"id": node_id},
        )
        return [{"_totals_by_type": counts, "_sample_size": len(nodes)}, *nodes]

    def neighborhood(self, node_type: str, node_id: str, depth: int = 2) -> dict[str, list[Any]]:
        depth = max(1, min(depth, 5))
        if self._driver is None:
            self.connect()
        if self._driver is None:
            return {"nodes": [], "edges": []}
        cypher = (
            f"MATCH path = (c:{node_type} {{id: $id}})-[*0..{depth}]-(n) "
            "WITH collect(DISTINCT n) AS nset, collect(DISTINCT relationships(path)) AS rsets "
            "UNWIND rsets AS rs UNWIND rs AS r "
            "WITH nset, collect(DISTINCT r) AS rels "
            "RETURN [x IN nset | {id: x.id, type: head(labels(x))}] AS nodes, "
            "[r IN rels | {source: startNode(r).id, source_type: head(labels(startNode(r))), "
            "target: endNode(r).id, target_type: head(labels(endNode(r))), relation: type(r)}] AS edges"
        )
        with self._driver.session() as session:
            rec = session.run(cypher, id=node_id).single()
            if not rec:
                return {"nodes": [], "edges": []}
            return {"nodes": rec["nodes"], "edges": rec["edges"]}

    def lineage_scope(
        self,
        node_type: str,
        node_id: str,
        up_depth: int = 3,
        down_depth: int = 3,
        max_paths: int = 400,
    ) -> dict[str, list[Any]]:
        """Only the directed upstream and downstream of a node — not its whole neighborhood.

        `neighborhood` walks undirected `-[*0..n]-` hops, which pulls in siblings that
        merely share a QVD and makes the rendered graph large and slow. Lineage answers
        "what feeds this" and "what does this feed", so each direction is traversed
        separately and only the relationships along those directed paths are returned.

        Every node is tagged with `direction` (root/upstream/downstream) so the UI can
        lay the graph out left-to-right instead of relying on force simulation.
        """
        up_depth = max(1, min(up_depth, 10))
        down_depth = max(1, min(down_depth, 10))
        if self._driver is None:
            self.connect()
        if self._driver is None:
            return {"nodes": [], "edges": []}

        def _edges(direction: str) -> list[dict[str, Any]]:
            depth = up_depth if direction == "upstream" else down_depth
            pattern = (
                f"(c:{node_type} {{id: $id}})<-[*1..{depth}]-(n)"
                if direction == "upstream"
                else f"(c:{node_type} {{id: $id}})-[*1..{depth}]->(n)"
            )
            cypher = (
                f"MATCH path = {pattern} "
                f"WITH relationships(path) AS rels, length(path) AS d "
                f"ORDER BY d ASC LIMIT {max_paths} "
                "UNWIND rels AS r "
                "RETURN DISTINCT startNode(r).id AS source, "
                "head(labels(startNode(r))) AS source_type, "
                "endNode(r).id AS target, head(labels(endNode(r))) AS target_type, "
                "type(r) AS relation"
            )
            return self._read(cypher, {"id": node_id})

        directions: dict[str, str] = {f"{node_type}::{node_id}": "root"}
        edges: list[dict[str, Any]] = []
        seen_edges: set[tuple[str, str, str, str, str]] = set()

        for direction in ("upstream", "downstream"):
            for row in _edges(direction):
                key = (
                    row["source_type"], row["source"], row["relation"],
                    row["target_type"], row["target"],
                )
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                edges.append(row)
                for side in ("source", "target"):
                    nkey = f"{row[f'{side}_type']}::{row[side]}"
                    if nkey not in directions:
                        directions[nkey] = direction

        nodes = [
            {"id": key.split("::", 1)[1], "type": key.split("::", 1)[0], "direction": value}
            for key, value in directions.items()
        ]
        return {"nodes": nodes, "edges": edges}

    def qvd_variants(self, filename: str) -> list[dict[str, Any]]:
        """All QVD nodes whose path ends with `filename`, with reader/writer counts.

        The same physical QVD can appear under several ids when a path contains an
        unresolved Qlik variable (e.g. `lib://$(vServer)/x/y.qvd` alongside
        `lib://QlikStorage/x/y.qvd`). Callers asking about "a QVD" by name almost always
        mean the union of those variants, so expose them together rather than letting the
        model pick one arbitrarily.
        """
        name = filename.strip().strip("[]'\"").replace("\\", "/").lower()
        name = name.rsplit("/", 1)[-1]
        cypher = (
            "MATCH (q:QVD) WHERE q.id ENDS WITH $suffix "
            "OPTIONAL MATCH (q)-[:READS]->(r:App) "
            "WITH q, count(DISTINCT r) AS readers "
            "OPTIONAL MATCH (w:App)-[:WRITES]->(q) "
            "RETURN q.id AS id, readers, count(DISTINCT w) AS writers "
            "ORDER BY readers DESC LIMIT 50"
        )
        rows = self._read(cypher, {"suffix": "/" + name})
        if not rows:
            return []
        total_cypher = (
            "MATCH (q:QVD) WHERE q.id ENDS WITH $suffix "
            "OPTIONAL MATCH (q)-[:READS]->(r:App) "
            "WITH collect(DISTINCT r) AS rs "
            "RETURN size(rs) AS distinct_reader_apps"
        )
        totals = self._read(total_cypher, {"suffix": "/" + name})
        distinct_readers = totals[0]["distinct_reader_apps"] if totals else None
        return [
            {
                "_summary": (
                    f"{len(rows)} path variant(s) of '{name}'. "
                    f"{distinct_readers} distinct apps read it across all variants. "
                    "Variants containing $(...) are unresolved Qlik variables pointing at "
                    "the same physical file; report the combined total, not a single row."
                ),
                "distinct_reader_apps_all_variants": distinct_readers,
            },
            *rows,
        ]

    def app_lineage(self, app_id: str) -> dict[str, list[str]]:
        """The exact inputs and outputs of one app, grouped by role.

        Documentation needs these as flat, deduplicated lists rather than the
        path-shaped output of `neighborhood`, and needs them to be exact -
        they are the one part of a generated document that is never inferred
        by a model.

        Edge directions follow the convention actually used in this graph
        (verified against the live database, not assumed):
        `(QVD|Table)-[:READS]->(App)`, `(App)-[:WRITES]->(QVD)`,
        `(App)-[:USES]->(Connection)`, `(App)-[:DEPENDS_ON]->(Table)`,
        `(Task)-[:RUNS]->(App)`. Getting these backwards yields an
        empty-but-successful result, which is worse than an error.

        Nodes carry only an `id` property, so app and task ids are returned
        here and resolved to display names by the caller from Postgres.
        """
        cypher = """
        MATCH (a:App {id: $id})
        OPTIONAL MATCH (q:QVD)-[:READS]->(a)
        WITH a, collect(DISTINCT q.id) AS qvds_read
        OPTIONAL MATCH (t:Table)-[:READS]->(a)
        WITH a, qvds_read, collect(DISTINCT t.id) AS tables_read
        OPTIONAL MATCH (a)-[:DEPENDS_ON]->(dt:Table)
        WITH a, qvds_read, tables_read, collect(DISTINCT dt.id) AS tables_depends_on
        OPTIONAL MATCH (a)-[:USES]->(c:Connection)
        WITH a, qvds_read, tables_read, tables_depends_on,
             collect(DISTINCT c.id) AS connections
        OPTIONAL MATCH (a)-[:WRITES]->(w:QVD)
        WITH a, qvds_read, tables_read, tables_depends_on, connections,
             collect(DISTINCT w.id) AS qvds_written
        OPTIONAL MATCH (a)-[:WRITES]->(:QVD)-[:READS]->(d:App)
        WHERE d.id <> a.id
        WITH a, qvds_read, tables_read, tables_depends_on, connections, qvds_written,
             collect(DISTINCT d.id) AS downstream_app_ids
        OPTIONAL MATCH (tk:Task)-[:RUNS]->(a)
        RETURN qvds_read, tables_read, tables_depends_on, connections, qvds_written,
               downstream_app_ids, collect(DISTINCT tk.id) AS task_ids
        """
        rows = self._read(cypher, {"id": app_id})
        empty: dict[str, list[str]] = {
            "qvds_read": [], "tables_read": [], "tables_depends_on": [],
            "connections": [], "qvds_written": [], "downstream_app_ids": [],
            "task_ids": [],
        }
        if not rows:
            return empty
        row = rows[0]
        return {k: [v for v in (row.get(k) or []) if v] for k in empty}

    def upstream_app_ids(self, app_id: str) -> list[str]:
        """Apps that write a QVD this app reads - its upstream producers."""
        cypher = (
            "MATCH (u:App)-[:WRITES]->(:QVD)-[:READS]->(a:App {id: $id}) "
            "WHERE u.id <> a.id RETURN collect(DISTINCT u.id) AS ids"
        )
        rows = self._read(cypher, {"id": app_id})
        return [v for v in (rows[0]["ids"] if rows else []) if v]

    def delete_app_edges(self, app_id: str) -> int:
        """Detach an app from its script-derived relationships, keeping the node.

        Called before re-inserting a changed app's edges so that dependencies removed
        from the load script disappear from the graph instead of lingering forever.
        Only script-derived relations are removed: OWNS/RUNS/BELONGS_TO come from QRS
        and would not be restored if that fetch failed during the same scan.
        """
        if self._driver is None:
            self.connect()
        if self._driver is None:
            return 0
        cypher = (
            "MATCH (a:App {id: $id})-[r]-() WHERE type(r) IN $rels "
            "WITH r LIMIT 100000 DELETE r RETURN count(r) AS removed"
        )
        with self._driver.session() as session:
            rec = session.run(cypher, id=app_id,
                              rels=list(SCRIPT_DERIVED_RELATIONS)).single()
            return int(rec["removed"]) if rec else 0

    def delete_app(self, app_id: str) -> None:
        """Remove an app node and its relationships (app deleted in Qlik)."""
        if self._driver is None:
            self.connect()
        if self._driver is None:
            return
        with self._driver.session() as session:
            session.run("MATCH (a:App {id: $id}) DETACH DELETE a", id=app_id)

    def prune_orphan_nodes(self) -> int:
        """Delete QVD/Table/Task/Connection nodes left with no relationships."""
        if self._driver is None:
            self.connect()
        if self._driver is None:
            return 0
        cypher = (
            "MATCH (n) WHERE (n:QVD OR n:Table OR n:Task OR n:Connection OR n:Owner OR n:Stream) "
            "AND NOT (n)--() "
            "WITH n LIMIT 100000 DELETE n RETURN count(n) AS removed"
        )
        with self._driver.session() as session:
            rec = session.run(cypher).single()
            return int(rec["removed"]) if rec else 0

    def run_cypher(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        assert_read_only(cypher)
        return self._read(self._ensure_limit(cypher), params or {})

    @staticmethod
    def _ensure_limit(cypher: str, default_limit: int = 200) -> str:
        """Append a LIMIT to ad-hoc queries that lack one.

        An unbounded model-authored query can return tens of thousands of rows, which
        overflows the LLM context window. Aggregate-only queries (a bare `RETURN count(...)`)
        return a single row and need no cap.
        """
        stripped = cypher.strip().rstrip(";").strip()
        if re.search(r"\bLIMIT\s+\d+\s*$", stripped, re.IGNORECASE):
            return stripped
        tail = stripped.rsplit("RETURN", 1)[-1] if "RETURN" in stripped.upper() else ""
        if tail and re.search(r"\b(count|sum|avg|min|max|collect)\s*\(", tail, re.IGNORECASE):
            if not re.search(r"\bAS\s+\w+\s*,", tail, re.IGNORECASE):
                return stripped
        return f"{stripped} LIMIT {default_limit}"

    # -- internals ------------------------------------------------------------
    def _read(self, cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if self._driver is None:
            self.connect()
        if self._driver is None:
            return []
        with self._driver.session() as session:
            result = session.run(cypher, **params)
            return [dict(record) for record in result]
