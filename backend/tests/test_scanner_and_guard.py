"""Tests for change_detector and Cypher read-only guard."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph.neo4j_client import assert_read_only
from app.lineage.builder import LineageBuilder
from app.models.entities import QlikApp, QlikTask, ScanObject
from app.scanner.change_detector import detect_changes


class TestChangeDetector(unittest.TestCase):
    def test_created_updated_deleted(self) -> None:
        previous = [
            ScanObject("app1", "h1", "App"),
            ScanObject("app2", "h2", "App"),
        ]
        current = [
            ScanObject("app1", "h1", "App"),       # unchanged
            ScanObject("app2", "h2-NEW", "App"),   # updated
            ScanObject("app3", "h3", "App"),       # created
        ]
        diff = detect_changes(previous, current)
        self.assertEqual({o.object_id for o in diff["created"]}, {"app3"})
        self.assertEqual({o.object_id for o in diff["updated"]}, {"app2"})
        # app1 was unchanged; nothing in either bucket
        self.assertEqual(diff["deleted"], [])


class TestTaskAndAppEdges(unittest.TestCase):
    def test_task_edges(self) -> None:
        tasks = [
            QlikTask("t1", "Nightly", app_id="app1", schedule_id="s1", depends_on_task_id="t0"),
        ]
        edges = LineageBuilder().build_task_edges(tasks)
        triples = {(e.source_type, e.relation, e.target_type) for e in edges}
        self.assertIn(("Task", "RUNS", "App"), triples)
        self.assertIn(("Task", "SCHEDULED_BY", "Schedule"), triples)
        self.assertIn(("Task", "DEPENDS_ON", "Task"), triples)

    def test_owner_and_stream_edges(self) -> None:
        apps = [QlikApp(app_id="app1", name="App One", owner_id="u1", stream_id="prod")]
        edges = LineageBuilder().build_app_metadata_edges(apps)
        triples = {(e.source_type, e.relation, e.target_type) for e in edges}
        self.assertIn(("Owner", "OWNS", "App"), triples)
        self.assertIn(("App", "BELONGS_TO", "Stream"), triples)


class TestCypherGuard(unittest.TestCase):
    def test_blocks_writes(self) -> None:
        for bad in [
            "CREATE (n:App {id: 'x'})",
            "MATCH (n) DELETE n",
            "MATCH (n) SET n.foo = 1",
            "MERGE (n:App {id: 'x'})",
            "MATCH (n) DETACH DELETE n",
            "DROP CONSTRAINT app_id",
        ]:
            with self.assertRaises(PermissionError, msg=f"should reject: {bad}"):
                assert_read_only(bad)

    def test_allows_reads(self) -> None:
        for good in [
            "MATCH (a:App)-[:READS]->(q:QVD) RETURN a, q LIMIT 10",
            "MATCH p=(t:Table)-[*1..3]->(a:App) RETURN p",
            "CALL db.labels()",
        ]:
            assert_read_only(good)  # should not raise


if __name__ == "__main__":
    unittest.main()
