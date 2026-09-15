"""Tests for the load-script condenser.

These lock in the behaviours that were found empirically against the real
estate of 1,759 apps - each one here corresponds to a pattern that actually
occurs in production scripts, not a hypothetical.
"""

from app.docs.condenser import condense, render


def test_splits_on_tab_markers():
    script = """///$tab Main
SET vX = 1;
///$tab Load Data
Sales:
LOAD a, b FROM [lib://Store/sales.qvd] (qvd);
"""
    c = condense(script)
    assert [s.name for s in c.sections] == ["Main", "Load Data"]


def test_script_without_tab_markers_still_parses():
    # 42 of 1,759 apps (2.4%) have no ///$tab markers at all.
    script = "Sales:\nLOAD a, b FROM [lib://Store/sales.qvd] (qvd);\n"
    c = condense(script)
    assert len(c.sections) == 1
    assert c.total_statements == 1


def test_field_lists_are_collapsed_to_a_count():
    script = """///$tab T
Big:
LOAD f1, f2, f3, f4, f5 FROM [lib://Store/big.qvd] (qvd);
"""
    c = condense(script)
    text = render(c, 1)
    assert "(5 fields)" in text
    assert "f3" not in text


def test_field_count_ignores_commas_inside_functions():
    # Num(X, '#,##0') must count as ONE field, not three.
    script = """///$tab T
T1:
LOAD Num(Amount, '#,##0') as Amt, Region FROM [lib://Store/x.qvd] (qvd);
"""
    c = condense(script)
    assert "(2 fields)" in render(c, 1)


def test_locale_boilerplate_variables_are_dropped():
    # Qlik injects this block into every app; it carries no business meaning.
    script = """///$tab Main
SET ThousandSep=',';
SET MonthNames='Jan;Feb;Mar';
SET DateFormat='M/D/YYYY';
SET vBusinessRegion = 'East';
"""
    c = condense(script)
    text = render(c, 1)
    assert "ThousandSep" not in text
    assert "MonthNames" not in text
    assert "vBusinessRegion" in text


def test_commented_out_code_is_not_reported_as_author_intent():
    # Whole sections of this estate are dead code. Surfacing those lines as
    # "intent" actively misleads the model that consumes this skeleton.
    script = """///$tab Dead
// LIB CONNECT TO 'old-connection';
// promo:
// LOAD
// division_description,
"""
    c = condense(script)
    text = render(c, 1)
    assert "LIB CONNECT" not in text
    assert "division_description" not in text


def test_fully_commented_section_is_flagged_as_disabled():
    # Distinguishing "does nothing" from "was switched off" matters when
    # documenting an app.
    script = """///$tab Dead
// promo:
// LOAD a,
// b FROM [lib://Store/x.qvd] (qvd);
///$tab Live
T:
LOAD a FROM [lib://Store/y.qvd] (qvd);
"""
    c = condense(script)
    dead, live = c.sections
    assert dead.disabled is True
    assert live.disabled is False
    assert "commented out" in render(c, 1)


def test_boilerplate_only_section_is_not_flagged_as_disabled():
    # `Main` is usually just the filtered locale block - it is not dead code.
    script = """///$tab Main
SET ThousandSep=',';
SET MonthNames='Jan;Feb;Mar';
SET DateFormat='M/D/YYYY';
"""
    c = condense(script)
    assert c.sections[0].disabled is False
    assert "commented out" not in render(c, 1)


def test_duplicate_section_names_are_all_preserved():
    # 158 of 1,759 apps (9%) reuse a section name. Keying sections by name
    # silently drops them and mixes up their comments.
    script = """///$tab Load
A:
LOAD a FROM [lib://Store/a.qvd] (qvd);
///$tab Load
B:
LOAD b FROM [lib://Store/b.qvd] (qvd);
"""
    c = condense(script)
    assert len(c.sections) == 2
    assert c.total_statements == 2


def test_semicolons_inside_quotes_do_not_split_statements():
    # MonthNames-style values contain semicolons; so do connection strings.
    script = """///$tab T
LET vList = 'a;b;c';
T:
LOAD x FROM [lib://Store/x.qvd] (qvd);
"""
    c = condense(script)
    assert c.total_statements == 2


def test_lib_urls_survive_comment_stripping():
    # `lib://` contains `//` and must not be treated as a line comment.
    script = """///$tab T
T:
LOAD a FROM [lib://QlikStorage/PRICING/x.qvd] (qvd);
"""
    text = render(condense(script), 1)
    assert "lib://QlikStorage/PRICING/x.qvd" in text


def test_store_targets_are_captured():
    script = """///$tab T
STORE T INTO [lib://Store/out.qvd] (qvd);
"""
    text = render(condense(script), 1)
    assert "out.qvd" in text


def test_render_levels_reduce_output_monotonically():
    script = """///$tab Main
// build the sales mart
SET vRegion = 'East';
Sales:
LOAD a, b, c FROM [lib://Store/sales.qvd] (qvd);
STORE Sales INTO [lib://Store/mart.qvd] (qvd);
"""
    c = condense(script)
    sizes = [len(render(c, lvl)) for lvl in (1, 2, 3, 4)]
    assert sizes == sorted(sizes, reverse=True), sizes


def test_very_large_script_is_condensed_aggressively():
    # The largest real script is 2.5M chars; it must collapse to a few KB.
    block = (
        "T{i}:\nLOAD a, b, c FROM [lib://Store/f{i}.qvd] (qvd);\n"
        "STORE T{i} INTO [lib://Store/o{i}.qvd] (qvd);\n"
    )
    script = "///$tab Bulk\n" + "".join(block.format(i=i) for i in range(5000))
    c = condense(script)
    out = render(c, 4)
    assert len(out) < len(script) * 0.01


def test_empty_script_does_not_raise():
    c = condense("")
    assert c.total_statements == 0
    render(c, 1)
