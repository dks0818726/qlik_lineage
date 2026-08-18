import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parser.qlik_parser import QlikScriptParser


class TestQlikScriptParser(unittest.TestCase):
    def test_extracts_expected_dependencies(self) -> None:
        fixture_dir = Path(__file__).parent / "fixtures"
        script = (fixture_dir / "sample_script.qvs").read_text(encoding="utf-8")
        expected = json.loads((fixture_dir / "expected_lineage.json").read_text(encoding="utf-8"))

        records = QlikScriptParser().parse(app_id="SalesDashboard", script=script)
        payload = [record.__dict__ for record in records]
        self.assertEqual(expected["dependencies"], payload)


if __name__ == "__main__":
    unittest.main()
