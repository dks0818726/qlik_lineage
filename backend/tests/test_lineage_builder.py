from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.lineage.builder import LineageBuilder
from app.models.entities import ParsedDependency


class TestLineageBuilder(unittest.TestCase):
    def test_builds_directed_edges(self) -> None:
        deps = [
            ParsedDependency(app_id="SalesDashboard", connection="Oracle", source_table="ORDERS"),
            ParsedDependency(app_id="SalesDashboard", input_qvd="Finance.qvd"),
            ParsedDependency(app_id="SalesDashboard", output_qvd="Orders.qvd"),
        ]

        edges = LineageBuilder().build_edges(deps)
        triples = {(e.source_type, e.relation, e.target_type) for e in edges}
        self.assertIn(("Table", "READS", "App"), triples)
        self.assertIn(("QVD", "READS", "App"), triples)
        self.assertIn(("App", "WRITES", "QVD"), triples)
        self.assertIn(("App", "USES", "Connection"), triples)


if __name__ == "__main__":
    unittest.main()
