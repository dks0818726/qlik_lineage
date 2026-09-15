"""Condense a Qlik load script down to its structural skeleton.

Why this exists
---------------
Documentation needs to know *what an app does*, not every field it selects.
Measured across all 1,759 scripts in this estate:

    median  12,431 chars   (~3,100 tokens)
    p90     58,420 chars   (~14,600 tokens)
    p99    193,364 chars   (~48,000 tokens)
    max  2,520,494 chars  (~630,000 tokens)

The prompt budget is 64,000 tokens, so pasting raw script text works for the
median app and fails catastrophically for the tail. Field lists inside LOAD
statements are the overwhelming majority of that volume and contribute almost
nothing to an explanation of purpose, so this module keeps the verbs and the
targets and collapses the field lists to a count.

This deliberately does NOT reuse ``QlikScriptParser``. That parser is tuned for
lineage extraction and discards exactly what documentation needs: section
boundaries, statement order, and author comments. Keeping them separate means
documentation work cannot destabilise verified lineage behaviour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# `///$tab Name` marks a script section. Present in 97.6% of scripts in this
# estate (1,717 / 1,759), averaging 12 sections each - a free, author-written
# outline of the app's own structure.
TAB_RE = re.compile(r"^\s*///\s*\$tab\s+(.*?)\s*$", re.MULTILINE)

BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# Strip `//` line comments but NOT the `//` inside `lib://` URLs.
LINE_COMMENT_RE = re.compile(r"(?m)(?<!:)//[^\n]*")

# A table label, e.g. `BlackFriday_Act_Fcst:` on its own line.
TABLE_LABEL_RE = re.compile(r"^\s*([A-Za-z_][\w\s\-\.]*?)\s*:\s*$", re.MULTILINE)

# Statement verbs worth keeping, in the order we test them.
LOAD_RE = re.compile(r"^\s*(?:(\w[\w\s]*?):\s*)?(LOAD|ADD\s+LOAD|BUFFER\s+LOAD)\b", re.IGNORECASE)
SQL_RE = re.compile(r"^\s*(?:SQL\s+)?(SELECT)\b", re.IGNORECASE)
STORE_RE = re.compile(r"^\s*STORE\s+(.+?)\s+INTO\s+(.+?)(?:\s*\(|;|$)", re.IGNORECASE | re.DOTALL)
CONNECT_RE = re.compile(r"^\s*(?:LIB\s+)?CONNECT\s+TO\s+(.+?)\s*;", re.IGNORECASE | re.DOTALL)
RESIDENT_RE = re.compile(r"\bRESIDENT\s+([\w\.\$\(\)\[\]]+)", re.IGNORECASE)
FROM_RE = re.compile(r"\bFROM\s+([^\s;（(]+)", re.IGNORECASE)
JOIN_RE = re.compile(r"^\s*((?:LEFT|RIGHT|INNER|OUTER)?\s*(?:JOIN|KEEP))\s*\(?\s*([\w\$\(\)]*)", re.IGNORECASE)
CONCAT_RE = re.compile(r"^\s*CONCATENATE\s*\(?\s*([\w\$\(\)]*)", re.IGNORECASE)
DROP_RE = re.compile(r"^\s*DROP\s+(TABLE|FIELD)S?\s+(.+?)\s*;", re.IGNORECASE | re.DOTALL)
SUB_RE = re.compile(r"^\s*SUB\s+([\w\.]+)", re.IGNORECASE)
CALL_RE = re.compile(r"^\s*CALL\s+([\w\.]+)", re.IGNORECASE)
SET_RE = re.compile(r"^\s*(SET|LET)\s+([\w\.]+)\s*=\s*(.*?)\s*;?\s*$", re.IGNORECASE | re.DOTALL)
INCLUDE_RE = re.compile(r"\$\(\s*(?:Must_)?Include\s*=\s*([^)]+)\)", re.IGNORECASE)

# Qlik injects this SET block into every new app. It is pure boilerplate: it
# describes locale formatting, never business logic, and appears in essentially
# all 1,759 scripts. Dropping it removes noise without losing meaning.
BOILERPLATE_VARS = {
    "thousandsep", "decimalsep", "moneythousandsep", "moneydecimalsep",
    "moneyformat", "timeformat", "dateformat", "timestampformat",
    "firstweekday", "brokenweeks", "referenceday", "firstmonthofyear",
    "collationlocale", "createsearchindexonreload", "monthnames",
    "longmonthnames", "daynames", "longdaynames", "numericalabbreviation",
    "nullinterpret", "hidesuffix", "hideprefix",
}


@dataclass
class Statement:
    """One meaningful script statement, reduced to verb + target."""
    kind: str          # LOAD | SQL | STORE | CONNECT | JOIN | DROP | SUB | CALL | VAR | INCLUDE
    summary: str       # human-readable one-liner
    fields: int = 0    # field count for LOAD/SELECT, 0 otherwise


@dataclass
class Section:
    """A `///$tab` section of the script."""
    name: str
    comments: list[str] = field(default_factory=list)
    statements: list[Statement] = field(default_factory=list)
    # True when the section contains only commented-out code. Without this the
    # section renders as empty, which reads as "does nothing" rather than
    # "was switched off" - a meaningful difference when documenting an app.
    disabled: bool = False

    @property
    def statement_count(self) -> int:
        return len(self.statements)


@dataclass
class CondensedScript:
    sections: list[Section]
    total_statements: int
    total_chars: int
    had_tabs: bool

    @property
    def section_count(self) -> int:
        return len(self.sections)


def _count_fields(body: str) -> int:
    """Count top-level comma-separated fields in a LOAD/SELECT body.

    Commas inside brackets or parentheses belong to function arguments such as
    ``Num(X, '#,##0')`` and must not inflate the count.
    """
    depth = 0
    count = 1 if body.strip() else 0
    for ch in body:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            count += 1
    return count


def _split_statements(text: str) -> list[str]:
    """Split on semicolons that are not inside quotes or brackets."""
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == ";" and depth == 0:
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _looks_like_code(text: str) -> bool:
    """True if a comment line is commented-out code rather than prose.

    Developers disable blocks by commenting them out, and whole sections of this
    estate are dead code. Feeding those lines to the model as "author intent"
    actively misleads it, so they are dropped.
    """
    s = text.strip()
    if not s:
        return True
    # Double-commented (`// // LOAD ...`) is always disabled code.
    if s.startswith("//"):
        return True
    # Dangling separators are mid-statement fragments, e.g. `division_desc,`
    if s.endswith((",", ":", ";", "(", "[")):
        return True
    first = re.split(r"[\s(,]", s, 1)[0].upper().strip("[]")
    if first in {
        "LOAD", "SELECT", "SQL", "SET", "LET", "STORE", "DROP", "JOIN", "LEFT",
        "RIGHT", "INNER", "OUTER", "CONCATENATE", "RESIDENT", "FROM", "CONNECT",
        "LIB", "SUB", "END", "CALL", "IF", "THEN", "ELSE", "FOR", "NEXT", "EXIT",
        "TRACE", "NOCONCATENATE", "WHERE", "GROUP", "ORDER", "INLINE", "AUTOGENERATE",
    }:
        return True
    # A bare identifier with no spaces carries no explanatory value.
    if " " not in s:
        return True
    return False


def _leading_comments(raw_section: str, limit: int = 3) -> list[str]:
    """Author comments at the top of a section - usually the intent statement.

    Only prose is kept; commented-out code is filtered out by _looks_like_code.
    """
    comments: list[str] = []
    scanned = 0
    for line in raw_section.splitlines():
        s = line.strip()
        if not s:
            continue
        scanned += 1
        # Only look at the head of the section; intent comments live at the top.
        if scanned > 25:
            break
        if s.startswith("//"):
            text = s.lstrip("/").strip()
            if text and not _looks_like_code(text):
                comments.append(text)
                if len(comments) >= limit:
                    break
        elif s.startswith("/*") or s.startswith("*"):
            text = s.strip("/*").strip()
            if text and not _looks_like_code(text):
                comments.append(text)
                if len(comments) >= limit:
                    break
    return comments


def _summarise(stmt: str) -> Statement | None:
    """Reduce one raw statement to a compact summary, or None if not useful."""
    flat = " ".join(stmt.split())
    if not flat:
        return None

    # -- variables ---------------------------------------------------------
    m = SET_RE.match(stmt)
    if m:
        kind_word, name, value = m.group(1).upper(), m.group(2), m.group(3)
        if name.lower() in BOILERPLATE_VARS:
            return None  # Qlik locale boilerplate, present in every app
        value = " ".join(value.split())
        if len(value) > 90:
            value = value[:90] + "..."
        return Statement("VAR", f"{kind_word} {name} = {value}")

    # -- includes ----------------------------------------------------------
    m = INCLUDE_RE.search(stmt)
    if m:
        return Statement("INCLUDE", f"Include {m.group(1).strip()}")

    # -- connections -------------------------------------------------------
    m = CONNECT_RE.match(stmt)
    if m:
        target = " ".join(m.group(1).split())
        if len(target) > 80:
            target = target[:80] + "..."
        return Statement("CONNECT", f"CONNECT TO {target}")

    # -- store -------------------------------------------------------------
    m = STORE_RE.match(stmt)
    if m:
        src = " ".join(m.group(1).split())
        dst = " ".join(m.group(2).split()).strip("[]")
        return Statement("STORE", f"STORE {src} -> {dst}")

    # -- drop --------------------------------------------------------------
    m = DROP_RE.match(stmt)
    if m:
        what = " ".join(m.group(2).split())
        if len(what) > 80:
            what = what[:80] + "..."
        return Statement("DROP", f"DROP {m.group(1).upper()} {what}")

    # -- sub / call --------------------------------------------------------
    m = SUB_RE.match(stmt)
    if m:
        return Statement("SUB", f"SUB {m.group(1)}")
    m = CALL_RE.match(stmt)
    if m:
        return Statement("CALL", f"CALL {m.group(1)}")

    # -- joins / concatenate ----------------------------------------------
    join_prefix = ""
    m = JOIN_RE.match(stmt)
    if m:
        join_prefix = " ".join(m.group(1).split()).upper()
        if m.group(2):
            join_prefix += f" ({m.group(2)})"
    else:
        m = CONCAT_RE.match(stmt)
        if m:
            join_prefix = "CONCATENATE"
            if m.group(1):
                join_prefix += f" ({m.group(1)})"

    # -- load / select -----------------------------------------------------
    load_m = LOAD_RE.search(stmt)
    sql_m = SQL_RE.search(stmt)
    if load_m or sql_m:
        label = load_m.group(1).strip() if (load_m and load_m.group(1)) else ""
        verb = "LOAD" if load_m else "SELECT"

        # Field list runs from after the verb to the FROM/RESIDENT keyword.
        start = (load_m or sql_m).end()
        rest = stmt[start:]
        cut = len(rest)
        for kw in (r"\bFROM\b", r"\bRESIDENT\b", r"\bAUTOGENERATE\b", r"\bINLINE\b"):
            km = re.search(kw, rest, re.IGNORECASE)
            if km and km.start() < cut:
                cut = km.start()
        n_fields = _count_fields(rest[:cut])

        source = ""
        rm = RESIDENT_RE.search(stmt)
        if rm:
            source = f"resident {rm.group(1)}"
        else:
            fm = FROM_RE.search(stmt)
            if fm:
                src = fm.group(1).strip().strip("[](),")
                if len(src) > 70:
                    src = "..." + src[-67:]
                source = f"from {src}"
            elif re.search(r"\bINLINE\b", stmt, re.IGNORECASE):
                source = "inline"
            elif re.search(r"\bAUTOGENERATE\b", stmt, re.IGNORECASE):
                source = "autogenerate"

        parts = [p for p in [join_prefix, f"{label}:" if label else "", verb] if p]
        summary = " ".join(parts)
        if n_fields:
            summary += f" ({n_fields} fields)"
        if source:
            summary += f" {source}"
        return Statement("SQL" if sql_m and not load_m else "LOAD", summary, n_fields)

    return None


def condense(script: str) -> CondensedScript:
    """Reduce a raw Qlik load script to its structural skeleton."""
    total_chars = len(script or "")
    if not script:
        return CondensedScript([], 0, 0, False)

    # Preserve `///$tab` markers while removing ordinary comments: temporarily
    # protect them, strip comments, then restore.
    tab_names = TAB_RE.findall(script)
    had_tabs = bool(tab_names)
    marker = "\x00TAB\x00"
    protected = TAB_RE.sub(lambda m: f"{marker}{m.group(1)}{marker}", script)

    # Keep a copy with comments for extracting author intent per section.
    with_comments = protected

    cleaned = BLOCK_COMMENT_RE.sub(" ", protected)
    cleaned = LINE_COMMENT_RE.sub(" ", cleaned)

    def _split_by_marker(text: str) -> list[tuple[str, str]]:
        parts = text.split(marker)
        out: list[tuple[str, str]] = []
        if not had_tabs:
            return [("Script", text)]
        # parts alternates: [pre, name, body, name, body, ...]
        if parts[0].strip():
            out.append(("Main", parts[0]))
        i = 1
        while i + 1 <= len(parts) - 1:
            out.append((parts[i].strip() or f"Section {len(out)+1}", parts[i + 1]))
            i += 2
        return out

    body_sections = _split_by_marker(cleaned)
    comment_sections = _split_by_marker(with_comments)

    sections: list[Section] = []
    total_statements = 0
    for idx, (name, body) in enumerate(body_sections):
        sec = Section(name=name)
        # Pair by INDEX, not by name: section names are author-chosen and are
        # frequently duplicated (or blank), so a name->body dict would silently
        # drop sections that share a name.
        raw_with_comments = comment_sections[idx][1] if idx < len(comment_sections) else ""
        sec.comments = _leading_comments(raw_with_comments)
        for raw in _split_statements(body):
            st = _summarise(raw)
            if st:
                sec.statements.append(st)
        total_statements += len(sec.statements)
        # A section with no live statements is either boilerplate we filtered
        # (e.g. the Qlik locale SET block in `Main`) or genuinely commented-out
        # code. Only the latter is worth flagging, so require that most
        # non-blank lines actually be comments rather than just checking length.
        if not sec.statements:
            body_lines = [ln.strip() for ln in raw_with_comments.splitlines() if ln.strip()]
            commented = [ln for ln in body_lines if ln.startswith("//")]
            if len(body_lines) >= 3 and len(commented) >= 0.7 * len(body_lines):
                sec.disabled = True
        # Keep a section even if empty - the name itself is information.
        sections.append(sec)

    return CondensedScript(
        sections=sections,
        total_statements=total_statements,
        total_chars=total_chars,
        had_tabs=had_tabs,
    )


# --------------------------------------------------------------------------- #
# Rendering at several levels of detail. The evidence assembler steps down this
# ladder until the pack fits the token budget.
# --------------------------------------------------------------------------- #

def render(condensed: CondensedScript, level: int = 1) -> str:
    """Render the skeleton as text.

    level 1 - full: every statement, comments, variables
    level 2 - no variable definitions
    level 3 - section names + per-kind counts only
    level 4 - section names only
    """
    lines: list[str] = []
    for sec in condensed.sections:
        lines.append(f"### Section: {sec.name}")
        if sec.disabled:
            lines.append("  (entirely commented out - not executed)")
            continue
        if level <= 2 and sec.comments:
            for c in sec.comments:
                lines.append(f"  # {c}")

        if level >= 4:
            lines.append(f"  ({sec.statement_count} statements)")
            continue

        if level == 3:
            kinds: dict[str, int] = {}
            for st in sec.statements:
                kinds[st.kind] = kinds.get(st.kind, 0) + 1
            if kinds:
                lines.append("  " + ", ".join(f"{v}x {k}" for k, v in sorted(kinds.items())))
            continue

        for st in sec.statements:
            if level == 2 and st.kind == "VAR":
                continue
            lines.append(f"  - {st.summary}")
    return "\n".join(lines)
