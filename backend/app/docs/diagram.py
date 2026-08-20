"""Deterministic ASCII data-flow diagram for a single app.

Rendered in code from graph edges rather than asked of the model, for two
reasons: it is guaranteed to match actual lineage instead of being
plausible-looking invention, and it costs zero output tokens.
"""

from __future__ import annotations

# Long QVD paths dominate the width of the diagram while the leaf filename
# carries nearly all of the meaning, so paths are shortened for display only.
MAX_LABEL = 46
MAX_ROWS = 8


def _leaf(path: str) -> str:
    """Filename portion of a QVD path, with the folder kept when it is short."""
    norm = path.replace("\\", "/").rstrip("/")
    parts = [p for p in norm.split("/") if p]
    if not parts:
        return path
    leaf = parts[-1]
    if len(parts) >= 2:
        candidate = f"{parts[-2]}/{leaf}"
        if len(candidate) <= MAX_LABEL:
            return candidate
    return leaf if len(leaf) <= MAX_LABEL else leaf[: MAX_LABEL - 3] + "..."


def _truncate(label: str) -> str:
    return label if len(label) <= MAX_LABEL else label[: MAX_LABEL - 3] + "..."


def _column(items: list[str], transform=_truncate) -> tuple[list[str], int]:
    """Render at most MAX_ROWS entries, summarising the remainder."""
    shown = [transform(i) for i in items[:MAX_ROWS]]
    hidden = len(items) - len(shown)
    return shown, hidden


def render_diagram(app_name: str, lineage: dict[str, list[str]],
                   downstream_names: list[str] | None = None,
                   upstream_names: list[str] | None = None) -> str:
    """An inputs -> app -> outputs flow diagram.

    `lineage` is the dict returned by `Neo4jClient.app_lineage`. Names for the
    downstream/upstream apps are passed in separately because graph nodes carry
    only ids.
    """
    inputs: list[tuple[str, str]] = []
    for q in lineage.get("qvds_read", []):
        inputs.append(("QVD", _leaf(q)))
    for t in lineage.get("tables_read", []):
        inputs.append(("TABLE", _truncate(t)))
    for c in lineage.get("connections", []):
        inputs.append(("CONN", _truncate(c)))

    outputs: list[tuple[str, str]] = [
        ("QVD", _leaf(q)) for q in lineage.get("qvds_written", [])
    ]

    lines: list[str] = []
    lines.append("```text")

    up = upstream_names or []
    if up:
        shown, hidden = _column(up)
        lines.append("UPSTREAM APPS")
        for name in shown:
            lines.append(f"  [{name}]")
        if hidden:
            lines.append(f"  ... and {hidden} more")
        lines.append("      |")
        lines.append("      v")

    lines.append("INPUTS")
    if inputs:
        shown_in = inputs[:MAX_ROWS]
        for kind, label in shown_in:
            lines.append(f"  ({kind}) {label}")
        if len(inputs) > len(shown_in):
            lines.append(f"  ... and {len(inputs) - len(shown_in)} more")
    else:
        lines.append("  (none recorded)")

    lines.append("      |")
    lines.append("      v")
    lines.append(f"  +{'-' * (min(len(app_name), MAX_LABEL) + 2)}+")
    lines.append(f"  | {_truncate(app_name)} |")
    lines.append(f"  +{'-' * (min(len(app_name), MAX_LABEL) + 2)}+")
    lines.append("      |")
    lines.append("      v")

    lines.append("OUTPUTS")
    if outputs:
        shown_out = outputs[:MAX_ROWS]
        for kind, label in shown_out:
            lines.append(f"  ({kind}) {label}")
        if len(outputs) > len(shown_out):
            lines.append(f"  ... and {len(outputs) - len(shown_out)} more")
    else:
        lines.append("  (none recorded - this app is a consumer, not a producer)")

    down = downstream_names or []
    if down:
        lines.append("      |")
        lines.append("      v")
        lines.append("DOWNSTREAM APPS")
        shown, hidden = _column(down)
        for name in shown:
            lines.append(f"  [{name}]")
        if hidden:
            lines.append(f"  ... and {hidden} more")

    lines.append("```")
    return "\n".join(lines)
