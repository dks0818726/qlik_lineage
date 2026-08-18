from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph.neo4j_upsert import bulk_upsert_payload, constraint_queries
from app.models.entities import GraphEdge


class TestGraphCreation(unittest.TestCase):
    def test_generates_constraints(self) -> None:
        queries = constraint_queries()
        self.assertTrue(any("App" in q for q in queries))
        self.assertTrue(any("QVD" in q for q in queries))

    def test_generates_upsert_query(self) -> None:
        edge = GraphEdge("Table", "ORDERS", "READS", "App", "SalesDashboard")
        payload = bulk_upsert_payload([edge])
        self.assertEqual(1, len(payload))
        query, params = payload[0]
        self.assertIn("MERGE (s:Table", query)
        self.assertEqual("ORDERS", params["source_id"])


if __name__ == "__main__":
    unittest.main()
