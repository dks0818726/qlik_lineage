from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, TypedDict

from app.agent.tools import AgentTools, TOOL_SCHEMAS
from app.config import settings

try:
    from litellm import completion
except Exception:  # pragma: no cover - optional at scaffold stage
    completion = None  # type: ignore[assignment]

try:
    from langgraph.graph import END, StateGraph
except Exception:  # pragma: no cover - langgraph optional
    StateGraph = None  # type: ignore[assignment]
    END = "__end__"

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "You are the Qlik Lineage Copilot. You are READ-ONLY: never propose reloads, edits, or writes. "
    "Answer questions about Qlik apps, QVDs, source tables, reload tasks, schedules, owners, and streams. "
    "Always call tools to gather grounded facts before answering. Cite node ids and types. "
    "If a question is ambiguous, briefly state your assumption then proceed.\n\n"
    "Neo4j graph schema. Edges point in the direction data FLOWS, so an app that reads a QVD is "
    "stored as (QVD)-[:READS]->(App), NOT (App)-[:READS]->(QVD). Use these exact patterns:\n"
    "  (QVD)-[:READS]->(App)          an app loads that QVD\n"
    "  (Table)-[:READS]->(App)        an app loads that source table\n"
    "  (App)-[:WRITES]->(QVD)         an app stores that QVD\n"
    "  (App)-[:DEPENDS_ON]->(Table)   an app derives that resident/temp table\n"
    "  (App)-[:USES]->(Connection)    an app uses that data connection\n"
    "  (Table)-[:BELONGS_TO]->(Connection)\n"
    "  (App)-[:BELONGS_TO]->(Stream)\n"
    "  (Owner)-[:OWNS]->(App)\n"
    "  (Task)-[:RUNS]->(App)          a reload task reloads that app\n"
    "\nWriting correct Cypher — these mistakes have all produced confidently wrong answers:\n"
    "1. Nodes have ONLY an `id` property. There is no `name`. Selecting n.name returns null. "
    "App ids are UUIDs, so after a graph query call search_apps/search_qvds/search_tables to "
    "turn ids into human-readable names before answering.\n"
    "2. Always use count(DISTINCT x), never count(x). The graph is many-to-many, so count(x) "
    "counts relationship rows and inflates the answer (one real query returned 6000 instead of 1190).\n"
    "3. Keep a traversal in ONE MATCH pattern. Do not split it across a WITH, which drops "
    "variables from scope and silently rebinds them. Write "
    "MATCH (p:App)-[:WRITES]->(q:QVD)-[:READS]->(c:App), not two MATCH clauses joined by WITH.\n"
    "4. A result of 0 or an empty list is far more often a wrong query than a real absence of "
    "data. Before reporting 'none found', re-check the arrow direction against the schema above "
    "and try again.\n"
    "5. The same physical QVD may exist under several ids when a path holds an unresolved Qlik "
    "variable, e.g. lib://$(vServer)/x/y.qvd and lib://QlikStorage/x/y.qvd. When the user names a "
    "QVD by filename, call qvd_variants FIRST and report the combined total across variants; "
    "picking one id can understate impact by more than 20x.\n"
    "6. Prefer aggregates over listing rows. Ask for counts, then a small sample (LIMIT 10-25). "
    "Ad-hoc queries without a LIMIT are automatically capped, and oversized tool results are "
    "truncated — if you see truncated:true, re-query with count(DISTINCT ...) for exact totals.\n"
    "\nAnswering style: give the direct answer first with concrete numbers, then a one-line note on "
    "how you derived it. If a result was truncated or ambiguous, say so plainly."
)


# Tool results are echoed back into the conversation, so an unbounded result set can
# overflow the model's context window. A real failure measured 185,906 tokens against a
# 64,000 limit. Cap rows and characters, and tell the model when truncation happened so
# it reports "at least N" rather than silently under-counting.
MAX_TOOL_RESULT_ROWS = 150
MAX_TOOL_RESULT_CHARS = 24000


def _serialise_tool_result(result: Any) -> str:
    payload: Any = result
    if isinstance(result, list) and len(result) > MAX_TOOL_RESULT_ROWS:
        payload = {
            "truncated": True,
            "total_rows": len(result),
            "showing_rows": MAX_TOOL_RESULT_ROWS,
            "note": (
                "Result truncated. Do not treat the listed rows as the complete set; "
                "re-query with an aggregate (count(DISTINCT ...)) if you need an exact total."
            ),
            "rows": result[:MAX_TOOL_RESULT_ROWS],
        }
    text = json.dumps(payload, default=str)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + (
            '..."}  [TRUNCATED: output exceeded the size limit. Re-query with a narrower '
            'filter, fewer returned fields, or an aggregate count.]'
        )
    return text


class AgentState(TypedDict, total=False):
    question: str
    messages: list[dict[str, Any]]
    answer: str
    trace: list[dict[str, Any]]


@dataclass
class LineageCopilotAgent:
    """LangGraph-orchestrated agent using LiteLLM for model routing + tool calling.

    Falls back to a deterministic retrieval-only response if LiteLLM is unavailable.
    """

    tools: AgentTools
    model: str = field(default_factory=lambda: settings.litellm_model)
    api_base: str = field(default_factory=lambda: settings.litellm_api_base)
    api_key: str = field(default_factory=lambda: settings.litellm_api_key)
    max_tool_iterations: int = 6

    def _completion_kwargs(self) -> dict[str, Any]:
        """Extra LiteLLM kwargs so a custom OpenAI-compatible endpoint is honoured."""
        kwargs: dict[str, Any] = {}
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.model.startswith("github_copilot/"):
            # The Copilot API rejects requests without editor/integration headers.
            # Credentials themselves are resolved by LiteLLM's cached device-code token.
            kwargs["extra_headers"] = {
                "editor-version": "vscode/1.85.1",
                "editor-plugin-version": "copilot/1.155.0",
                "Copilot-Integration-Id": "vscode-chat",
            }
        return kwargs

    # -- public API -----------------------------------------------------------
    def answer(self, question: str) -> dict[str, Any]:
        if completion is None:
            return self._fallback(question)
        state: AgentState = {
            "question": question,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            "trace": [],
        }
        graph = self._build_graph()
        try:
            if graph is None:
                final = self._run_loop(state)
            else:
                final = graph.invoke(state)
        except Exception as exc:  # noqa: BLE001 - model/provider unreachable
            logger.warning("LLM call failed (%s); returning retrieval-only answer", exc)
            return self._fallback(question, reason=str(exc))
        return {
            "question": question,
            "model": self.model,
            "answer": final.get("answer", ""),
            "trace": final.get("trace", []),
        }

    # -- graph ----------------------------------------------------------------
    def _build_graph(self):
        if StateGraph is None:
            return None
        builder = StateGraph(AgentState)
        builder.add_node("model", self._node_model)
        builder.add_node("tools", self._node_tools)
        builder.set_entry_point("model")
        builder.add_conditional_edges("model", self._route, {"tools": "tools", "end": END})
        builder.add_edge("tools", "model")
        return builder.compile()

    def _route(self, state: AgentState) -> str:
        last = state["messages"][-1]
        return "tools" if last.get("tool_calls") else "end"

    def _node_model(self, state: AgentState) -> AgentState:
        response = completion(
            model=self.model,
            messages=state["messages"],
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            temperature=0.1,
            **self._completion_kwargs(),
        )
        message = response["choices"][0]["message"]
        msg_dict: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"],
                    },
                }
                for tc in tool_calls
            ]
            state["trace"].append({"step": "model.tool_calls", "tool_calls": msg_dict["tool_calls"]})
        else:
            state["answer"] = msg_dict["content"]
            state["trace"].append({"step": "model.answer", "content": msg_dict["content"]})
        state["messages"].append(msg_dict)
        return state

    def _node_tools(self, state: AgentState) -> AgentState:
        last = state["messages"][-1]
        for call in last.get("tool_calls", []):
            name = call["function"]["name"]
            raw_args = call["function"]["arguments"] or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {}
            try:
                result = self.tools.dispatch(name, args)
            except Exception as exc:
                result = {"error": str(exc)}
            state["trace"].append({"step": "tool.result", "tool": name, "args": args, "result": result})
            state["messages"].append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": name,
                    "content": _serialise_tool_result(result),
                }
            )
        return state

    # -- manual loop (when langgraph absent) ---------------------------------
    def _run_loop(self, state: AgentState) -> AgentState:
        for _ in range(self.max_tool_iterations):
            self._node_model(state)
            if not state["messages"][-1].get("tool_calls"):
                return state
            self._node_tools(state)
        state.setdefault("answer", "Reached maximum tool iterations without final answer.")
        return state

    # -- fallback -------------------------------------------------------------
    def _fallback(self, question: str, reason: str = "") -> dict[str, Any]:
        context = {
            "apps": self.tools.search_apps(question),
            "qvds": self.tools.search_qvds(question),
            "tables": self.tools.search_tables(question),
            "tasks": self.tools.search_tasks(question),
        }
        detail = reason or "LiteLLM unavailable"
        return {
            "question": question,
            "model": self.model,
            "answer": f"LLM unavailable ({detail}); returning retrieval context only.",
            "context": context,
            "trace": [],
        }
