from __future__ import annotations

import re
from typing import Iterable

from app.models.entities import ParsedDependency


# --- Regexes -----------------------------------------------------------------
LIB_CONNECT_RE = re.compile(
    r"\bLIB\s+CONNECT\s+TO\s+['\"]?([A-Za-z0-9_./:\\ -]+?)['\"]?\s*;",
    re.IGNORECASE,
)
# ODBC / OLEDB / CUSTOM CONNECT (Qlik legacy connection strings)
ODBC_CONNECT_RE = re.compile(
    r"\b(ODBC|OLEDB|CUSTOM)\s+CONNECT(?:32|64)?\s+TO\s+['\"]?([^;'\"]+?)['\"]?\s*(?:\([^)]*\))?\s*;",
    re.IGNORECASE,
)
SQL_SELECT_RE = re.compile(
    r"\bSQL\s+SELECT\b.*?\bFROM\s+([\[\"`]?[A-Za-z0-9_.$#\-]+(?:\.[A-Za-z0-9_.$#\-]+)*[\]\"`]?)",
    re.IGNORECASE | re.DOTALL,
)
# A single identifier part: backtick/bracket/double-quote quoted, or bare.
# The bare form allows embedded `$(var)` segments so that a partially resolved
# reference like `$(vPath).sales_v` is captured whole instead of collapsing to
# a bare `$` and losing the table name.
_IDENT_PART = r"(?:`[^`]+`|\[[^\]]+\]|\"[^\"]+\"|(?:\$\([^)]*\)|[A-Za-z0-9_$#\-])+)"
# A (possibly multi-part) table reference, e.g. catalog.schema.table,
# `project.dataset`.`table`, [db].[dbo].[tbl].
TABLE_REF_RE = re.compile(rf"{_IDENT_PART}(?:\s*\.\s*{_IDENT_PART})*")
SQL_KEYWORD_RE = re.compile(r"(FROM|JOIN)\b", re.IGNORECASE)
# SQL scalar functions that take a FROM inside their argument list, e.g.
# EXTRACT(DAY FROM col) — the following token is a column, never a table.
_FROM_ARG_FUNCTIONS = {"extract", "substring", "trim", "overlay", "position"}
# Tokens that are never real tables when they follow FROM/JOIN.
_NON_TABLE_TOKENS = {"select", "unnest", "lateral", "table", "values", "dual"}
# A SQL pass-through statement. Qlik accepts a bare `SELECT` after a
# `LIB CONNECT` as well as the explicit `SQL SELECT` prefix, and the bare form
# is by far the more common of the two in practice - one real app used it in 25
# of its 26 SQL statements, so requiring the `SQL` keyword found 2 tables where
# there were 26. Statements loading from a file or QVD are matched earlier and
# never reach this test.
SQL_STATEMENT_RE = re.compile(r"\bSELECT\b", re.IGNORECASE)
# File suffixes that mark a FROM target as a file rather than a table.
_FILE_EXTENSIONS = {
    "qvd", "csv", "txt", "xls", "xlsx", "xlsm", "xml", "json", "parquet",
    "qvx", "dat", "tsv", "htm", "html", "kml", "log",
}

STORE_RE = re.compile(
    r"\bSTORE\s+(?:\*\s+FROM\s+)?([A-Za-z0-9_]+)\s+INTO\s+\[?\s*(lib:\/\/[^\];]+|[A-Za-z0-9_./:\\\-$() ]+\.(?:qvd|csv|txt|parquet))\s*\]?\s*(?:\([^)]*\))?\s*;",
    re.IGNORECASE,
)
LOAD_FROM_QVD_RE = re.compile(
    r"\bLOAD\b.*?\bFROM\s+\[?\s*(lib:\/\/[^\];]+\.qvd|[A-Za-z0-9_./:\\\-$() ]+?\.qvd)\s*\]?",
    re.IGNORECASE | re.DOTALL,
)
LOAD_FROM_FILE_RE = re.compile(
    r"\bLOAD\b.*?\bFROM\s+\[?\s*(lib:\/\/[^\];]+\.(?:csv|txt|xlsx|xls|parquet|qvx)|[A-Za-z0-9_./:\\\-$() ]+?\.(?:csv|txt|xlsx|xls|parquet|qvx))\s*\]?",
    re.IGNORECASE | re.DOTALL,
)
RESIDENT_RE = re.compile(
    r"\bLOAD\b.*?\bRESIDENT\s+([A-Za-z0-9_]+)",
    re.IGNORECASE | re.DOTALL,
)
INCLUDE_RE = re.compile(
    r"\$\(\s*(?:must_)?include\s*=\s*([^)]+?)\s*\)",
    re.IGNORECASE,
)
BINARY_LOAD_RE = re.compile(
    r"\bBINARY\s+\[?\s*([^\];]+?)\s*\]?\s*;",
    re.IGNORECASE,
)
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# Qlik's `REM <text>;` comment statement: everything up to the terminating
# semicolon is a comment. Anchored to a statement start so a field or variable
# merely containing "rem" is never stripped.
REM_COMMENT_RE = re.compile(r"(?im)(?:(?<=;)|(?<=\A)|(?<=\n))\s*REM\b[^;]*;")
# `SET name = value;` / `LET name = value;` — Qlik variable definitions. Paths in
# real scripts are usually assembled from these (e.g. lib://$(vQlikStorageDirectory)/...).
SET_VAR_RE = re.compile(
    r"^(?:SET|LET)\s+([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*(.*?)\s*;?$",
    re.IGNORECASE | re.DOTALL,
)
VAR_REF_RE = re.compile(r"\$\(([^()$]*)\)")# Strip line comments but DO NOT eat the `//` inside `lib://...` URLs (negative lookbehind on `:`).
LINE_COMMENT_RE = re.compile(r"(?m)(?<!:)//[^\n]*|(?<![\w-])--[^\n]*")


class QlikScriptParser:
    """Extracts lineage-relevant dependencies from Qlik script text.

    Handles multi-line statements, block/line comments, quoted/bracketed paths,
    lib:// references, INCLUDE/MUST_INCLUDE, RESIDENT, STORE, and LOAD FROM qvd.

    Emitted QVD paths and table names are *canonicalised* so the same physical
    object always yields the same node id. Qlik/Windows paths are
    case-insensitive and treat ``\\`` and ``/`` interchangeably, so without this
    an app that STOREs ``dir\\X.qvd`` would never link to an app that LOADs
    ``dir/x.qvd``.
    """

    @staticmethod
    def canonical_qvd(path: str) -> str:
        """Canonical id for a QVD/file path: forward slashes, lower case."""
        return path.strip().strip("[]\"'").replace("\\", "/").lower()

    @staticmethod
    def canonical_table(name: str) -> str:
        """Canonical id for a database table reference (case-insensitive)."""
        return name.strip().lower()

    @staticmethod
    def _expand_vars(text: str, variables: dict[str, str], depth: int = 5) -> str:
        """Substitute ``$(vName)`` with values captured from SET/LET statements.

        Resolution is iterative because variables commonly nest, e.g.
        ``SET vPath = lib://$(vServer)/extract``. Unknown variables are left
        untouched so the raw form remains visible in the graph.
        """
        for _ in range(depth):
            if "$(" not in text:
                break
            expanded = VAR_REF_RE.sub(
                lambda m: variables.get(m.group(1).strip().lower(), m.group(0)), text
            )
            if expanded == text:
                break
            text = expanded
        return text

    @staticmethod
    def _is_literal_value(value: str) -> bool:
        """True if a SET/LET value is a plain literal we can safely substitute.

        Qlik ``LET`` evaluates expressions (``Date(Today())``); we cannot compute
        those, so anything still containing a call after stripping variable
        references is rejected rather than producing a bogus path.
        """
        return "(" not in VAR_REF_RE.sub("", value)

    def parse(self, app_id: str, script: str) -> list[ParsedDependency]:
        cleaned = self._strip_comments(script)
        statements = self._iter_statements(cleaned)

        current_connection: str | None = None
        variables: dict[str, str] = {}
        dependencies: list[ParsedDependency] = []

        for stmt in statements:
            normalized = " ".join(stmt.split())

            # Qlik executes sequentially, so record variables as we encounter
            # them; later statements resolve against the values seen so far.
            var_match = SET_VAR_RE.match(normalized)
            if var_match:
                value = var_match.group(2).strip().strip("'\"")
                if self._is_literal_value(value):
                    variables[var_match.group(1).strip().lower()] = self._expand_vars(
                        value, variables
                    )
                continue

            for inc in INCLUDE_RE.finditer(normalized):
                dependencies.append(
                    ParsedDependency(
                        app_id=app_id,
                        include_file=self._expand_vars(
                            inc.group(1).strip().strip("'\""), variables
                        ),
                        statement=normalized,
                    )
                )

            lib_match = LIB_CONNECT_RE.search(normalized)
            if lib_match:
                current_connection = lib_match.group(1).strip()
                dependencies.append(
                    ParsedDependency(
                        app_id=app_id,
                        connection=current_connection,
                        statement=normalized,
                    )
                )
                continue

            odbc_match = ODBC_CONNECT_RE.search(normalized)
            if odbc_match:
                # Group 2 is the raw connection string; take the DSN/Provider hint.
                conn_str = odbc_match.group(2).strip()
                current_connection = conn_str.split(";")[0].strip() or conn_str
                dependencies.append(
                    ParsedDependency(
                        app_id=app_id,
                        connection=current_connection,
                        statement=normalized,
                    )
                )
                continue

            binary_match = BINARY_LOAD_RE.search(normalized)
            if binary_match:
                dependencies.append(
                    ParsedDependency(
                        app_id=app_id,
                        include_file=binary_match.group(1).strip(),
                        statement=normalized,
                    )
                )
                continue

            store_match = STORE_RE.search(normalized)
            if store_match:
                dependencies.append(
                    ParsedDependency(
                        app_id=app_id,
                        output_qvd=self.canonical_qvd(
                            self._expand_vars(store_match.group(2), variables)
                        ),
                        statement=normalized,
                    )
                )
                continue

            qvd_match = LOAD_FROM_QVD_RE.search(normalized)
            if qvd_match:
                dependencies.append(
                    ParsedDependency(
                        app_id=app_id,
                        input_qvd=self.canonical_qvd(
                            self._expand_vars(qvd_match.group(1), variables)
                        ),
                        statement=normalized,
                    )
                )
                continue

            file_match = LOAD_FROM_FILE_RE.search(normalized)
            if file_match:
                dependencies.append(
                    ParsedDependency(
                        app_id=app_id,
                        input_qvd=self.canonical_qvd(
                            self._expand_vars(file_match.group(1), variables)
                        ),
                        connection=current_connection,
                        statement=normalized,
                    )
                )
                continue

            if SQL_STATEMENT_RE.search(normalized):
                tables = self._sql_table_refs(self._expand_vars(normalized, variables))
                if tables:
                    for source_table in tables:
                        dependencies.append(
                            ParsedDependency(
                                app_id=app_id,
                                connection=current_connection,
                                source_table=self.canonical_table(source_table),
                                statement=normalized,
                            )
                        )
                    continue

            resident_match = RESIDENT_RE.search(normalized)
            if resident_match:
                dependencies.append(
                    ParsedDependency(
                        app_id=app_id,
                        resident_table=resident_match.group(1),
                        statement=normalized,
                    )
                )
                continue

        return dependencies

    # ------------------------------------------------------------------------
    def _normalize_table_ref(self, raw: str) -> str:
        """Turn a possibly-quoted multi-part reference into `a.b.c`.

        ``\u0060project.dataset\u0060.\u0060table\u0060`` and ``[db].[dbo].[tbl]`` both collapse to
        their unquoted dotted form. Quoted segments may themselves contain dots
        (BigQuery writes ``\u0060project.dataset\u0060``), which stay as separators.
        """
        parts: list[str] = []
        buf: list[str] = []
        quote: str | None = None
        closing = {"`": "`", "[": "]", '"': '"'}
        for ch in raw.strip():
            if quote:
                if ch == quote:
                    quote = None
                else:
                    buf.append(ch)
                continue
            if ch in closing:
                quote = closing[ch]
                continue
            if ch == ".":
                parts.append("".join(buf).strip())
                buf = []
                continue
            if ch.isspace():
                continue
            buf.append(ch)
        parts.append("".join(buf).strip())
        return ".".join(p for p in parts if p)

    def _looks_like_path(self, ref: str) -> bool:
        """True when a FROM target is a file or connection URL, not a table.

        Now that bare `SELECT` statements are parsed, a preceding LOAD that
        reads a file can share a statement with its SELECT, so a `lib://...`
        or a filename can reach the table extractor. Those are already handled
        as QVD/file dependencies and must not also be recorded as tables.
        """
        low = ref.lower()
        if "://" in low or low.startswith("lib") and "/" in low:
            return True
        if "/" in ref or "\\" in ref:
            return True
        return low.rsplit(".", 1)[-1] in _FILE_EXTENSIONS

    def _is_unresolved_ref(self, ref: str) -> bool:
        """True when nothing but unresolved Qlik variables remains.

        `$(vPath)` carries no table name at all; `$(vPath).sales_v` does.
        """
        literal = re.sub(r"\$\([^)]*\)", "", ref)
        return not any(c.isalnum() for c in literal)

    def _sql_table_refs(self, sql: str) -> list[str]:
        """All real tables referenced by FROM/JOIN clauses in a SQL statement.

        Walks the statement tracking parenthesis nesting so that:
          * ``EXTRACT(DAY FROM col)`` does not yield ``col`` as a table,
          * ``FROM (SELECT ... FROM real_tbl)`` still yields ``real_tbl``,
          * every joined table is captured, not just the first FROM.
        """
        refs: list[str] = []
        seen: set[str] = set()
        func_stack: list[str] = []
        i, n = 0, len(sql)
        while i < n:
            ch = sql[i]
            if ch == "(":
                j = i - 1
                while j >= 0 and sql[j].isspace():
                    j -= 1
                k = j
                while k >= 0 and (sql[k].isalnum() or sql[k] == "_"):
                    k -= 1
                func_stack.append(sql[k + 1 : j + 1].lower())
                i += 1
                continue
            if ch == ")":
                if func_stack:
                    func_stack.pop()
                i += 1
                continue
            if (ch in "fFjJ") and (i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] == "_")):
                match = SQL_KEYWORD_RE.match(sql, i)
                if match:
                    keyword = match.group(1).lower()
                    inside_from_func = (
                        keyword == "from" and func_stack and func_stack[-1] in _FROM_ARG_FUNCTIONS
                    )
                    rest = sql[match.end() :].lstrip()
                    # `FROM (` is a subquery/derived table: skip it, but keep
                    # walking so the inner FROM/JOIN clauses are still captured.
                    if not inside_from_func and not rest.startswith("("):
                        ref_match = TABLE_REF_RE.match(rest)
                        if ref_match:
                            table = self._normalize_table_ref(ref_match.group(0))
                            if (
                                table
                                and table.lower() not in _NON_TABLE_TOKENS
                                and not self._looks_like_path(table)
                                # `FROM $(vPath)` names the table entirely
                                # through an unresolved variable, so there is
                                # no usable node. A partially resolved ref such
                                # as `$(vPath).sales_v` still carries the table
                                # name and is worth recording.
                                and not self._is_unresolved_ref(table)
                                and table not in seen
                            ):
                                seen.add(table)
                                refs.append(table)
                    i = match.end()
                    continue
            i += 1
        return refs

    def _strip_comments(self, script: str) -> str:
        no_block = BLOCK_COMMENT_RE.sub(" ", script)
        no_lines = LINE_COMMENT_RE.sub("", no_block)
        return REM_COMMENT_RE.sub(" ", no_lines)

    def _iter_statements(self, script: str) -> Iterable[str]:
        """Split on `;` while keeping lib:// paths together (they don't contain `;`)."""
        buf: list[str] = []
        for ch in script:
            buf.append(ch)
            if ch == ";":
                stmt = "".join(buf).strip()
                if stmt and stmt != ";":
                    yield stmt
                buf = []
        tail = "".join(buf).strip()
        if tail:
            yield tail
