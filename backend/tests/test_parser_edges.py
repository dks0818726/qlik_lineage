"""Parser edge cases: multi-line SQL, block comments, lib://, includes, multiple stores."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parser.qlik_parser import QlikScriptParser


class TestQlikScriptParserEdges(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = QlikScriptParser()

    def test_multiline_sql_select(self) -> None:
        script = """
        LIB CONNECT TO 'Oracle Prod';

        Orders:
        SQL SELECT
            o.id,
            o.customer_id,
            o.amount
        FROM
            SALES.ORDERS o
        WHERE o.year = 2024;
        """
        deps = self.parser.parse("app1", script)
        sources = {d.source_table for d in deps if d.source_table}
        self.assertIn("sales.orders", sources)
        conns = {d.connection for d in deps if d.connection}
        self.assertIn("Oracle Prod", conns)

    def test_block_and_line_comments_are_stripped(self) -> None:
        script = """
        /* Big block comment
           with multiple lines
           STORE FakeTable INTO Fake.qvd;
        */
        // STORE FakeLine INTO FakeLine.qvd;
        -- SQL inline comment
        LIB CONNECT TO RealConn;
        SQL SELECT * FROM REAL_TABLE;
        STORE Real INTO Real.qvd;
        """
        deps = self.parser.parse("app1", script)
        qvds = {d.output_qvd for d in deps if d.output_qvd}
        self.assertEqual({"real.qvd"}, qvds)
        self.assertNotIn("fake.qvd", qvds)
        self.assertNotIn("fakeline.qvd", qvds)

    def test_lib_qvd_paths(self) -> None:
        script = """
        LOAD * FROM [lib://DataFiles/Master/Customers.qvd] (qvd);
        STORE Customers INTO [lib://DataFiles/Master/Customers_Out.qvd] (qvd);
        """
        deps = self.parser.parse("app1", script)
        ins = {d.input_qvd for d in deps if d.input_qvd}
        outs = {d.output_qvd for d in deps if d.output_qvd}
        self.assertTrue(any("lib://" in q and "customers.qvd" in q for q in ins))
        self.assertTrue(any("lib://" in q and "customers_out.qvd" in q for q in outs))

    def test_include_directive(self) -> None:
        script = "$(Include='lib://Scripts/common.qvs');\nSQL SELECT 1 FROM DUAL;"
        deps = self.parser.parse("app1", script)
        includes = [d.include_file for d in deps if d.include_file]
        self.assertTrue(any("common.qvs" in inc for inc in includes))

    def test_must_include_directive(self) -> None:
        script = "$(Must_Include=lib://Scripts/init.qvs);"
        deps = self.parser.parse("app1", script)
        includes = [d.include_file for d in deps if d.include_file]
        self.assertTrue(any("init.qvs" in inc for inc in includes))

    def test_multiple_stores_in_one_script(self) -> None:
        script = """
        STORE A INTO A.qvd;
        STORE B INTO B.qvd;
        STORE C INTO [lib://DataFiles/C.qvd] (qvd);
        """
        deps = self.parser.parse("app1", script)
        qvds = {d.output_qvd for d in deps if d.output_qvd}
        self.assertGreaterEqual(len(qvds), 3)

    def test_resident_table(self) -> None:
        script = """
        Stage:
        LOAD * FROM [src.qvd];

        Final:
        LOAD a, b
        RESIDENT Stage;
        """
        deps = self.parser.parse("app1", script)
        residents = {d.resident_table for d in deps if d.resident_table}
        self.assertIn("Stage", residents)


class TestSqlTableExtraction(unittest.TestCase):
    """Real-world SQL shapes found in the production Qlik corpus."""

    def setUp(self) -> None:
        self.parser = QlikScriptParser()

    def _sources(self, script: str) -> set[str]:
        return {d.source_table for d in self.parser.parse("app1", script) if d.source_table}

    def test_bigquery_backticked_project_dataset_table(self) -> None:
        script = "SQL SELECT * FROM `d-wardar-306877093.reporting`.`fa_cmm_complete_base`;"
        self.assertEqual(
            {"d-wardar-306877093.reporting.fa_cmm_complete_base"}, self._sources(script)
        )

    def test_fully_backticked_three_part_name(self) -> None:
        script = "SQL SELECT * FROM `entdata.rpt.qlik_dim_ecom_order_sku_vw`;"
        self.assertEqual({"entdata.rpt.qlik_dim_ecom_order_sku_vw"}, self._sources(script))

    def test_bracketed_parts(self) -> None:
        script = "SQL SELECT * FROM [entdata].[rpt].[dks_sku];"
        self.assertEqual({"entdata.rpt.dks_sku"}, self._sources(script))

    def test_joined_tables_are_captured(self) -> None:
        script = (
            "SQL SELECT a.id FROM plm_staging.samplerequest a "
            "INNER JOIN plm_staging.sample b ON a.id = b.id "
            "LEFT JOIN `entdata.ecm.inv_vendor_prod_atp` c ON c.id = a.id;"
        )
        self.assertEqual(
            {
                "plm_staging.samplerequest",
                "plm_staging.sample",
                "entdata.ecm.inv_vendor_prod_atp",
            },
            self._sources(script),
        )

    def test_extract_from_is_not_a_table(self) -> None:
        script = (
            "SQL SELECT MOD(EXTRACT(DAYOFWEEK FROM all_days), 7) AS business_day_ind "
            "FROM reporting.calendar_dim;"
        )
        self.assertEqual({"reporting.calendar_dim"}, self._sources(script))

    def test_subquery_yields_inner_table_not_placeholder(self) -> None:
        script = "SQL SELECT * FROM (SELECT id FROM dbo.submissions) wrap;"
        self.assertEqual({"dbo.submissions"}, self._sources(script))

    def test_alias_is_not_part_of_table_name(self) -> None:
        script = "SQL SELECT o.id FROM SALES.ORDERS o WHERE o.year = 2024;"
        self.assertEqual({"sales.orders"}, self._sources(script))

    def test_table_case_variants_collapse_to_one_id(self) -> None:
        script = "SQL SELECT * FROM AAM.WORKLIST; SQL SELECT * FROM aam.worklist;"
        self.assertEqual({"aam.worklist"}, self._sources(script))

    def test_qvd_path_separator_and_case_collapse(self) -> None:
        """A STORE with backslashes must produce the same node id as a LOAD with slashes."""
        stored = self.parser.parse(
            "writer", r"STORE X INTO [lib://QlikStorage\EBIR\Extract\Dim\E_DATE_DIM.qvd];"
        )
        loaded = self.parser.parse(
            "reader", "LOAD * FROM [lib://QlikStorage/EBIR/Extract/Dim/e_date_dim.qvd];"
        )
        out = {d.output_qvd for d in stored if d.output_qvd}
        inp = {d.input_qvd for d in loaded if d.input_qvd}
        self.assertEqual(out, inp)
        self.assertEqual({"lib://qlikstorage/ebir/extract/dim/e_date_dim.qvd"}, out)


class TestVariableExpansion(unittest.TestCase):
    """Qlik scripts assemble paths from SET/LET variables; they must resolve."""

    def setUp(self) -> None:
        self.parser = QlikScriptParser()

    def test_storage_variable_is_expanded(self) -> None:
        script = """
        SET vQlikStorageDirectory = 'QlikStorage - PROD';
        LOAD * FROM [lib://$(vQlikStorageDirectory)/ebir/extract/dim/E_DATE_DIM.qvd];
        """
        deps = self.parser.parse("app1", script)
        self.assertEqual(
            {"lib://qlikstorage - prod/ebir/extract/dim/e_date_dim.qvd"},
            {d.input_qvd for d in deps if d.input_qvd},
        )

    def test_nested_variables_resolve(self) -> None:
        script = """
        SET vServer = 'QlikStorage';
        SET vQvdDir = lib://$(vServer)/ebir/extract;
        STORE Out INTO [$(vQvdDir)/dim/E_PRODUCT.qvd];
        """
        deps = self.parser.parse("app1", script)
        self.assertEqual(
            {"lib://qlikstorage/ebir/extract/dim/e_product.qvd"},
            {d.output_qvd for d in deps if d.output_qvd},
        )

    def test_writer_and_reader_converge_on_same_node(self) -> None:
        """The whole point: a STORE and a LOAD of one file must share an id."""
        writer = self.parser.parse(
            "w",
            "SET vTargetServer = 'QlikStorage';\n"
            r"STORE T INTO [lib://$(vTargetServer)\EBIR\Extract\Dim\E_DATE_DIM.qvd];",
        )
        reader = self.parser.parse(
            "r",
            "SET vServer = 'QlikStorage';\n"
            "LOAD * FROM [lib://$(vServer)/ebir/extract/dim/e_date_dim.qvd];",
        )
        self.assertEqual(
            {d.output_qvd for d in writer if d.output_qvd},
            {d.input_qvd for d in reader if d.input_qvd},
        )

    def test_unresolvable_expression_is_left_alone(self) -> None:
        """LET with a function call cannot be evaluated; never fabricate a path."""
        script = """
        LET vToday = Date(Today());
        LOAD * FROM [lib://Storage/snap_$(vToday).qvd];
        """
        deps = self.parser.parse("app1", script)
        paths = {d.input_qvd for d in deps if d.input_qvd}
        self.assertEqual({"lib://storage/snap_$(vtoday).qvd"}, paths)

    def test_unknown_variable_is_preserved(self) -> None:
        script = "LOAD * FROM [lib://$(vDefinedElsewhere)/x.qvd];"
        deps = self.parser.parse("app1", script)
        self.assertEqual(
            {"lib://$(vdefinedelsewhere)/x.qvd"}, {d.input_qvd for d in deps if d.input_qvd}
        )

    def test_variable_in_sql_table_is_expanded(self) -> None:
        script = """
        SET vProjectId = 'p-wardar-381480765';
        SQL SELECT * FROM `$(vProjectId).reporting`.`fa_cmm_complete_base`;
        """
        deps = self.parser.parse("app1", script)
        self.assertEqual(
            {"p-wardar-381480765.reporting.fa_cmm_complete_base"},
            {d.source_table for d in deps if d.source_table},
        )


class TestCommentHandling(unittest.TestCase):
    """Commented-out statements must never produce lineage."""

    def setUp(self) -> None:
        self.parser = QlikScriptParser()

    def _qvds(self, script: str) -> set[str]:
        deps = self.parser.parse("app1", script)
        return {d.output_qvd or d.input_qvd for d in deps if d.output_qvd or d.input_qvd}

    def test_double_slash_comment_ignored(self) -> None:
        self.assertEqual(set(), self._qvds("// STORE X INTO [lib://S/fake.qvd];"))

    def test_double_dash_comment_ignored(self) -> None:
        self.assertEqual(set(), self._qvds("-- STORE X INTO [lib://S/fake.qvd];"))

    def test_rem_statement_ignored(self) -> None:
        """Qlik's REM comment runs until the terminating semicolon."""
        self.assertEqual(set(), self._qvds("REM STORE X INTO [lib://S/fake.qvd];"))

    def test_rem_does_not_eat_following_statements(self) -> None:
        script = "REM this is a note; STORE X INTO [lib://S/real.qvd];"
        self.assertEqual({"lib://s/real.qvd"}, self._qvds(script))

    def test_field_containing_rem_is_not_stripped(self) -> None:
        script = "LOAD REMAINDER_QTY FROM [lib://S/remainder.qvd];"
        self.assertEqual({"lib://s/remainder.qvd"}, self._qvds(script))

    def test_trailing_comment_keeps_real_statement(self) -> None:
        script = "STORE X INTO [lib://S/real.qvd]; // STORE Y INTO [lib://S/fake.qvd];"
        self.assertEqual({"lib://s/real.qvd"}, self._qvds(script))

    def test_lib_url_is_not_mistaken_for_a_comment(self) -> None:
        self.assertEqual(
            {"lib://storage/keep.qvd"}, self._qvds("LOAD * FROM [lib://Storage/keep.qvd];")
        )

    def test_commented_sql_select_yields_no_table(self) -> None:
        deps = self.parser.parse("app1", "// SQL SELECT * FROM fake.commented_table;")
        self.assertEqual(set(), {d.source_table for d in deps if d.source_table})


if __name__ == "__main__":
    unittest.main()
