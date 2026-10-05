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

    def test_mount_spellings_of_one_qvd_yield_single_edges(self) -> None:
        from app.parser.qlik_parser import QlikScriptParser

        script = """
        LOAD * FROM [lib://$(vServer)/ebir/extract/dim/e_product_dim.qvd] (qvd);
        LOAD * FROM [lib://QlikStorage/ebir/extract/dim/E_PRODUCT_DIM.qvd] (qvd);
        LOAD * FROM [ebir/extract/dim/e_product_dim.qvd] (qvd);
        STORE T INTO [lib://$(vTargetServer)/ebir/extract/dim/e_product_dim.qvd] (qvd);
        STORE T INTO [lib://qlikstorage/ebir/extract/dim/e_product_dim.qvd] (qvd);
        """
        deps = QlikScriptParser().parse("app1", script)
        edges = LineageBuilder().build_edges(deps)
        qvd = "lib://qlikstorage/ebir/extract/dim/e_product_dim.qvd"
        reads = [e for e in edges if e.relation == "READS" and e.source_id == qvd]
        writes = [e for e in edges if e.relation == "WRITES" and e.target_id == qvd]
        self.assertEqual(1, len(reads))
        self.assertEqual(1, len(writes))


if __name__ == "__main__":
    unittest.main()
