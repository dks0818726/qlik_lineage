"""Assembles the size-bounded evidence pack that documentation is generated from.

The naive approach - pasting the raw load script into the prompt - works for the
median app and fails hard for the tail. Measured across all 1,759 scripts the
median is 12,431 characters but the maximum is 2,520,494, roughly ten times the
entire prompt budget on its own.

So the pack is built from three sources that are already in the database:
identity (Postgres), exact lineage facts (Neo4j), and a condensed script
skeleton. If the result still exceeds the budget it is degraded through a
ladder of progressively coarser levels rather than truncated mid-sentence, and
the level actually used is recorded so a reader always knows how complete the
evidence was.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.docs.condenser import CondensedScript, condense, render

logger = logging.getLogger(__name__)

# Rungs of the degradation ladder, matching `render`'s levels.
LEVEL_FULL = 1
LEVEL_NO_VARS = 2
LEVEL_COUNTS = 3
LEVEL_NAMES = 4
LEVEL_LABELS = {
    LEVEL_FULL: "full skeleton",
    LEVEL_NO_VARS: "skeleton without variable definitions",
    LEVEL_COUNTS: "section names with statement counts",
    LEVEL_NAMES: "section names only",
}

# How many lineage items to list before collapsing to a count. Long QVD path
# lists are the second-largest contributor to pack size after the script.
MAX_LINEAGE_ITEMS = 40


def estimate_tokens(text: str, model: str | None = None) -> int:
    """Token count for `text`, preferring an exact count when available.

    `litellm.token_counter` is used when importable. The character-based
    fallback under-estimates on dense script text, so a 15% margin is applied
    to that path only - over-estimating costs a little evidence, while
    under-estimating costs the whole request.
    """
    try:
        import litellm

        return int(litellm.token_counter(model=model or "gpt-4o", text=text))
    except Exception:
        return int(len(text) / 4 * 1.15)


@dataclass
class EvidencePack:
    app_id: str
    app_name: str
    text: str
    level: int
    tokens: int
    section_count: int
    statement_count: int
    lineage: dict[str, list[str]] = field(default_factory=dict)
    downstream_names: list[str] = field(default_factory=list)
    upstream_names: list[str] = field(default_factory=list)
    identity: dict[str, Any] = field(default_factory=dict)

    @property
    def level_label(self) -> str:
        return LEVEL_LABELS.get(self.level, str(self.level))


def _format_list(title: str, items: list[str]) -> str:
    if not items:
        return f"{title}: none recorded"
    shown = items[:MAX_LINEAGE_ITEMS]
    body = "\n".join(f"  - {i}" for i in shown)
    if len(items) > len(shown):
        body += f"\n  - ... and {len(items) - len(shown)} more"
    return f"{title} ({len(items)}):\n{body}"


def _lineage_block(lineage: dict[str, list[str]],
                   downstream_names: list[str],
                   upstream_names: list[str]) -> str:
    parts = [
        "## Lineage facts (exact, from the lineage graph - do not contradict these)",
        _format_list("QVD files read", lineage.get("qvds_read", [])),
        _format_list("Source tables read", lineage.get("tables_read", [])),
        _format_list("Tables referenced in the script",
                     lineage.get("tables_depends_on", [])),
        _format_list("Data connections used", lineage.get("connections", [])),
        _format_list("QVD files written", lineage.get("qvds_written", [])),
        _format_list("Upstream apps (produce data this app reads)", upstream_names),
        _format_list("Downstream apps (consume data this app writes)", downstream_names),
    ]
    return "\n".join(parts)


def _identity_block(identity: dict[str, Any]) -> str:
    rows = [
        f"App name: {identity.get('name')}",
        f"App ID: {identity.get('app_id')}",
        f"Owner: {identity.get('owner') or 'unknown'}",
        f"Stream: {identity.get('stream') or 'none'}",
        f"Last modified: {identity.get('modified_at') or 'unknown'}",
        f"Reload tasks: {', '.join(identity.get('tasks') or []) or 'none'}",
    ]
    return "## App identity\n" + "\n".join(rows)


def build_pack(
    app_id: str,
    identity: dict[str, Any],
    lineage: dict[str, list[str]],
    downstream_names: list[str],
    upstream_names: list[str],
    script_text: str | None,
    budget_tokens: int,
    model: str | None = None,
) -> EvidencePack:
    """Assemble the pack, degrading the skeleton until it fits `budget_tokens`."""
    condensed: CondensedScript = condense(script_text or "")

    identity_text = _identity_block(identity)
    lineage_text = _lineage_block(lineage, downstream_names, upstream_names)
    # Identity and lineage are small and are the parts that must never be
    # dropped - they are the factual backbone the model is told not to
    # contradict. Only the skeleton is degraded.
    fixed = f"{identity_text}\n\n{lineage_text}\n\n## Load script structure\n"
    fixed_tokens = estimate_tokens(fixed, model)

    chosen_level = LEVEL_NAMES
    chosen_body = ""
    chosen_tokens = fixed_tokens
    for level in (LEVEL_FULL, LEVEL_NO_VARS, LEVEL_COUNTS, LEVEL_NAMES):
        body = render(condensed, level)
        total = fixed_tokens + estimate_tokens(body, model)
        if total <= budget_tokens:
            chosen_level, chosen_body, chosen_tokens = level, body, total
            break
        # Remember the coarsest attempt so an over-budget app still gets
        # something rather than an error.
        chosen_level, chosen_body, chosen_tokens = level, body, total

    if chosen_tokens > budget_tokens:
        # Even section names alone are too large. Hard-trim as a last resort;
        # this is recorded in the footer so the reader knows evidence was cut.
        max_chars = max(0, int((budget_tokens - fixed_tokens) * 3))
        chosen_body = chosen_body[:max_chars] + "\n... (evidence truncated)"
        chosen_tokens = fixed_tokens + estimate_tokens(chosen_body, model)
        logger.warning("Evidence pack for %s truncated at level %s", app_id, chosen_level)

    text = fixed + chosen_body
    return EvidencePack(
        app_id=app_id,
        app_name=str(identity.get("name") or app_id),
        text=text,
        level=chosen_level,
        tokens=chosen_tokens,
        section_count=condensed.section_count,
        statement_count=condensed.total_statements,
        lineage=lineage,
        downstream_names=downstream_names,
        upstream_names=upstream_names,
        identity=identity,
    )
