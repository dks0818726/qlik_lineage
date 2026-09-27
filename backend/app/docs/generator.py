"""Generates a technical document describing what a Qlik app does.

Two design points matter here.

**The LLM call is isolated from the chat loop.** If documentation were produced
inside the agent's normal tool loop the finished markdown would be echoed back
as a tool result and re-sent on every subsequent turn, so a 1,500-token document
would cost 1,500 tokens on every following message for the rest of the session.
Instead this service makes its own `completion()` call with a fresh message list
and returns only a small receipt to the conversation.

**The header table and the diagram are not model-generated.** They are filled
deterministically from the database, so they are guaranteed to match actual
lineage rather than being plausible-looking invention, and they cost no output
tokens.
"""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.docs.diagram import render_diagram
from app.docs.evidence import EvidencePack, build_pack

try:
    from litellm import RateLimitError, completion
except Exception:  # pragma: no cover - litellm optional at import time
    completion = None

    class RateLimitError(Exception):
        pass

logger = logging.getLogger(__name__)

DIAGRAM_PLACEHOLDER = "{{DATA_FLOW_DIAGRAM}}"

DOCGEN_PROMPT = """You are a data engineer documenting a Qlik Sense application \
for colleagues who have never opened it.

You are given exact lineage facts extracted from the platform and a condensed \
skeleton of the app's load script. Write a technical document using EXACTLY the \
seven section headings below, in this order, and nothing else.

## Overview
What this app does and why it exists, in 2-4 sentences. Infer the business \
purpose from the app name, the section names, and the data it moves.

## Data sources
Where the data comes from. Group by kind (QVD files, database tables, \
connections). Explain what each group appears to contain.

## Transformations
What the script actually does to the data, following the script sections in \
order. Describe joins, aggregations, filters and incremental-load patterns.

## Output tables / fields
What the app produces and who it is for.

## Dependencies
Upstream apps it relies on and downstream apps that rely on it, plus reload \
tasks. State clearly if it is a leaf or a root of the chain.

## Data flow diagram
Write EXACTLY this placeholder on its own line and nothing else in this \
section: {placeholder}

## Notes & recommendations
Anything a maintainer should know: disabled sections, unresolved variable paths, \
fragile patterns, or gaps in the evidence.

RULES
- Never contradict the lineage facts. They are exact.
- If evidence is missing for a section, say so plainly. Do not invent \
table names, field names, schedules or owners.
- Sections marked "entirely commented out - not executed" are disabled code. \
Mention them under Notes, never as active behaviour.
- Paths containing $(...) are unresolved Qlik variables. Call that out rather \
than treating them as literal paths.
- Be concise. Prose, not bullet-point padding.

EVIDENCE
{evidence}
"""


def slugify(name: str, app_id: str) -> str:
    """Filesystem-safe, unique filename for an app.

    The short app id is appended because app names in this estate are not
    unique and several differ only by characters that slugify away.
    """
    base = re.sub(r"[^A-Za-z0-9]+", "_", name or "app").strip("_") or "app"
    return f"{base[:80]}__{app_id[:8]}.md"


@dataclass
class DocumentationResult:
    app_id: str
    app_name: str
    filename: str
    path: str
    markdown: str
    bytes: int
    sections: int
    evidence_level: int
    evidence_label: str
    model: str
    stale: bool = False


class AppNotFound(Exception):
    """No app matched the supplied name or id."""


class AmbiguousApp(Exception):
    """Several apps matched the supplied name.

    Raised rather than silently picking the first match: documenting the wrong
    app is worse than asking which one was meant, and app names in this estate
    are not unique.
    """

    def __init__(self, query: str, candidates: list[dict[str, Any]]) -> None:
        self.query = query
        self.candidates = candidates
        names = "; ".join(f"{c['name']} ({c['app_id'][:8]})" for c in candidates[:8])
        super().__init__(
            f"{len(candidates)} apps match '{query}': {names}. "
            "Ask the user which one, or pass the exact app_id."
        )


class DocumentationGenerator:
    """Builds evidence, calls the model once, writes the file, stores the result."""

    def __init__(self, repo, graph) -> None:
        self.repo = repo
        self.graph = graph

    # -- resolution -----------------------------------------------------------
    def resolve_app(self, query: str) -> dict[str, Any]:
        """Find an app from either an app id or an app name.

        Users ask for "document the Sales Dashboard", not for a GUID, so the
        same entry point accepts both. Matching is tried strongest-first -
        exact id, exact name, then a contains search - so that an exact name
        is never beaten by a longer partial match that happens to contain it.
        """
        q = (query or "").strip().strip("'\"")
        if not q:
            raise AppNotFound("No app name or id supplied")

        exact = self.repo.get_app(q)
        if exact:
            return exact

        with self.repo._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT app_id, name FROM apps WHERE lower(name) = lower(%s) "
                "ORDER BY updated_at DESC",
                (q,),
            )
            rows = cur.fetchall()
            if not rows:
                cur.execute(
                    "SELECT app_id, name FROM apps WHERE name ILIKE %s "
                    "ORDER BY length(name), updated_at DESC LIMIT 25",
                    (f"%{q}%",),
                )
                rows = cur.fetchall()

        if not rows:
            raise AppNotFound(
                f"No app found matching '{query}'. Try search_apps to find the exact name."
            )
        if len(rows) > 1:
            raise AmbiguousApp(query, rows)

        app = self.repo.get_app(rows[0]["app_id"])
        if not app:
            raise AppNotFound(f"App '{query}' disappeared during lookup")
        return app

    # -- evidence -------------------------------------------------------------
    def _app_names(self, app_ids: list[str]) -> list[str]:
        """Resolve app ids to display names; graph nodes carry only ids."""
        names: list[str] = []
        for app_id in app_ids:
            row = self.repo.get_app(app_id)
            names.append(row["name"] if row and row.get("name") else app_id)
        return names

    def _identity(self, app: dict[str, Any], task_ids: list[str]) -> dict[str, Any]:
        owner = app.get("owner_id")
        stream = app.get("stream_id")
        tasks: list[str] = list(task_ids)

        def unresolved(value: str | None) -> str:
            """Label an id we could not resolve, rather than printing a bare GUID.

            The `owners` table is currently empty in this deployment, so owner
            ids do not resolve. Presenting the raw GUID under a heading of
            "Owner" reads as if it were data; this makes the gap explicit so
            neither the model nor a reader mistakes it for a person.
            """
            return "unknown" if not value else f"unknown (id {value[:8]})"

        try:
            with self.repo._conn() as conn, conn.cursor() as cur:

                def lookup(table: str, id_col: str, value: str | None) -> str | None:
                    if not value:
                        return None
                    cur.execute(
                        f"SELECT name FROM {table} WHERE {id_col} = %s", (value,)
                    )
                    row = cur.fetchone()
                    return row["name"] if row and row.get("name") else None

                owner = lookup("owners", "owner_id", owner) or unresolved(owner)
                stream = lookup("streams", "stream_id", stream) or unresolved(stream)
                tasks = [
                    lookup("tasks", "task_id", tid) or unresolved(tid)
                    for tid in task_ids
                ]
        except Exception:  # pragma: no cover - identity is decoration, not evidence
            logger.warning("Could not resolve owner/stream/task names", exc_info=True)
            owner, stream = unresolved(owner), unresolved(stream)
            tasks = [unresolved(t) for t in task_ids]

        return {
            "app_id": app.get("app_id"),
            "name": app.get("name"),
            "owner": owner,
            "stream": stream,
            "modified_at": app.get("modified_at"),
            "tasks": tasks,
        }

    def build_evidence(self, app_ref: str) -> tuple[EvidencePack, dict[str, Any]]:
        app = self.resolve_app(app_ref)
        app_id = app["app_id"]
        lineage = self.graph.app_lineage(app_id)
        downstream = self._app_names(lineage.get("downstream_app_ids", []))
        upstream = self._app_names(self.graph.upstream_app_ids(app_id))
        identity = self._identity(app, lineage.get("task_ids", []))
        script = self.repo.get_script(app_id)
        pack = build_pack(
            app_id=app_id,
            identity=identity,
            lineage=lineage,
            downstream_names=downstream,
            upstream_names=upstream,
            script_text=script,
            budget_tokens=settings.docgen_max_evidence_tokens,
            model=settings.effective_docgen_model,
        )
        return pack, app

    # -- rendering ------------------------------------------------------------
    def _header(self, identity: dict[str, Any]) -> str:
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        tasks = ", ".join(identity.get("tasks") or []) or "none"
        return "\n".join([
            f"# {identity.get('name')}",
            "",
            "| | |",
            "|---|---|",
            f"| App ID | `{identity.get('app_id')}` |",
            f"| Owner | {identity.get('owner') or 'unknown'} |",
            f"| Stream | {identity.get('stream') or 'none'} |",
            f"| Last modified | {identity.get('modified_at') or 'unknown'} |",
            f"| Reload task | {tasks} |",
            f"| Generated | {generated} |",
            "",
            "",
        ])

    def _footer(self, pack: EvidencePack, model: str) -> str:
        # A blank line before the rule is required, otherwise Markdown treats
        # `---` following a text line as a setext heading underline.
        return "\n".join([
            "",
            "",
            "---",
            f"*Generated from lineage scan data using `{model}`. "
            f"Evidence completeness: {pack.level_label}. "
            f"Sections analysed: {pack.section_count}. "
            f"Statements analysed: {pack.statement_count}.*",
            "",
        ])

    def _completion_kwargs(self, model: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if settings.litellm_api_base:
            kwargs["api_base"] = settings.litellm_api_base
        if settings.litellm_api_key:
            kwargs["api_key"] = settings.litellm_api_key
        if model.startswith("github_copilot/"):
            kwargs["extra_headers"] = {
                "editor-version": "vscode/1.85.1",
                "editor-plugin-version": "copilot/1.155.0",
                "Copilot-Integration-Id": "vscode-chat",
            }
        return kwargs

    @staticmethod
    def _retry_after_seconds(exc: RateLimitError) -> float | None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if not headers:
            return None

        for name in ("x-ms-retry-after-ms", "retry-after-ms"):
            value = headers.get(name)
            if value is not None:
                try:
                    return max(0.0, float(value) / 1000.0)
                except (TypeError, ValueError):
                    logger.warning("Ignoring invalid %s header: %r", name, value)

        value = headers.get("retry-after")
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                now = datetime.now(retry_at.tzinfo)
                return max(0.0, (retry_at - now).total_seconds())
            except (TypeError, ValueError, OverflowError):
                logger.warning("Ignoring invalid Retry-After header: %r", value)
                return None

    def _retry_delay(self, exc: RateLimitError, retry_number: int) -> float:
        max_delay = max(0.0, settings.docgen_retry_max_seconds)
        provider_delay = self._retry_after_seconds(exc)
        if provider_delay is not None:
            return provider_delay

        base = max(0.0, settings.docgen_retry_base_seconds)
        exponential = min(base * (2 ** (retry_number - 1)), max_delay)
        return random.uniform(exponential / 2.0, exponential)

    def _call_model(self, pack: EvidencePack, model: str) -> str:
        if completion is None:
            raise RuntimeError("litellm is not installed; cannot generate documentation")
        prompt = DOCGEN_PROMPT.format(
            placeholder=DIAGRAM_PLACEHOLDER, evidence=pack.text
        )
        max_retries = max(0, settings.docgen_max_retries)
        for attempt in range(max_retries + 1):
            try:
                resp = completion(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=settings.docgen_max_output_tokens,
                    temperature=0.2,
                    num_retries=0,
                    **self._completion_kwargs(model),
                )
                return (resp.choices[0].message.content or "").strip()
            except RateLimitError as exc:
                if attempt >= max_retries:
                    logger.error(
                        "Documentation generation rate-limited after %d attempts",
                        attempt + 1,
                    )
                    raise
                retry_number = attempt + 1
                delay = self._retry_delay(exc, retry_number)
                logger.warning(
                    "Documentation generation rate-limited; retrying in %.2fs "
                    "(retry %d/%d)",
                    delay,
                    retry_number,
                    max_retries,
                )
                time.sleep(delay)

        raise RuntimeError("Documentation generation retry loop exited unexpectedly")

    @staticmethod
    def _strip_leading_title(body: str) -> str:
        """Remove a top-level heading the model added.

        The header table already supplies the `# <App Name>` title, and models
        reliably repeat it, producing two H1s in the finished document.
        """
        lines = body.lstrip().splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and lines[0].lstrip().startswith("# "):
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)
        return "\n".join(lines)

    # -- public API -----------------------------------------------------------
    def generate(self, app_ref: str) -> DocumentationResult:
        """Generate documentation for an app given its name or its id."""
        pack, app = self.build_evidence(app_ref)
        app_id = app["app_id"]
        model = settings.effective_docgen_model
        body = self._call_model(pack, model)
        body = self._strip_leading_title(body)

        diagram = render_diagram(
            pack.app_name, pack.lineage, pack.downstream_names, pack.upstream_names
        )
        if DIAGRAM_PLACEHOLDER in body:
            body = body.replace(DIAGRAM_PLACEHOLDER, diagram)
        else:
            # The model dropped or reworded the placeholder. Append the diagram
            # rather than losing it - it is the one section guaranteed correct.
            logger.info("Diagram placeholder missing for %s; appending section", app_id)
            body = f"{body}\n\n## Data flow diagram\n\n{diagram}"

        markdown = self._header(pack.identity) + body + self._footer(pack, model)
        sections = markdown.count("\n## ")

        filename = slugify(pack.app_name, app_id)
        out_dir = Path(settings.docgen_output_dir)
        path = out_dir / filename
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")
        except OSError:
            # The document is still stored in Postgres and downloadable, so a
            # read-only or missing mount degrades the feature rather than
            # failing the request.
            logger.warning("Could not write %s; serving from database only",
                           path, exc_info=True)

        self.repo.upsert_documentation(
            app_id=app_id,
            markdown=markdown,
            filename=filename,
            script_hash_value=app.get("script_hash"),
            model=model,
            evidence_level=pack.level,
            sections=sections,
        )

        return DocumentationResult(
            app_id=app_id,
            app_name=pack.app_name,
            filename=filename,
            path=str(path),
            markdown=markdown,
            bytes=len(markdown.encode("utf-8")),
            sections=sections,
            evidence_level=pack.level,
            evidence_label=pack.level_label,
            model=model,
        )
