# Qlik Lineage Copilot — Workflow Explanation

How the current code works, end to end — the existing workflow (no changes).

## 1. Startup (what happens when the backend boots)
`backend/app/main.py` `@app.on_event("startup")` does **only schema creation**:
- `get_repository().init_schema()` → creates Postgres tables (`schema.sql`).
- `get_neo4j().init_schema()` → creates Neo4j uniqueness constraints.

It does **not** load any Qlik data and does **not** start any scheduler (`SCAN_INTERVAL_SECONDS` is unused). So a fresh boot = empty knowledge base.

## 2. Populating the DBs (the "scan" = your initialization)
Triggered manually via `POST /scan/sync` (blocking) or `POST /scan/run` (background). Both call `ScannerOrchestrator.run(mode)` in `scanner/orchestrator.py`:

```
QRS REST  ──► fetch_apps, fetch_reload_tasks, fetch_data_connections, fetch_owners, fetch_streams
Engine WS ──► get_load_script(app_id)  (per app)
   │
   ├─ detect_changes()  → full = all apps; delta = only changed (by modifiedDate/script_hash)
   ├─ QlikScriptParser.parse(script)  → lineage deps (SQL SELECT, lib://, STORE, RESIDENT, includes…)
   ├─ LineageBuilder.build_edges()    → typed edges (Table→App READS, App→QVD WRITES, Task→App RUNS…)
   ├─ PostgresRepository.upsert_*()   → apps, scripts, qvds, tables, connections, tasks, edges, change_log
   ├─ Neo4jClient.upsert_edges()      → graph nodes + relationships
   └─ event_bus.publish_sync()        → realtime events on /ws/lineage + /events
```

Result: Postgres = searchable metadata; Neo4j = the lineage graph. Both live in Docker volumes (`postgres_data`, `neo4j_data`), so they **survive reboots** — that's your persistent knowledge base.

## 3. How the chat window drives the agent
Frontend `ChatPage.tsx` → `api.ask(question)` (`api.ts`) → `POST /agent/ask` → `agent.py` → `LineageCopilotAgent.answer()` (`workflow.py`):

```
question
  │  messages = [SYSTEM_PROMPT(read-only), user question]
  ▼
LangGraph state machine (model ⇄ tools loop, max 6 iterations)
  ├─ model node: litellm.completion(model=LITELLM_MODEL, tools=TOOL_SCHEMAS, tool_choice="auto")
  │     └─ if the model requests tool calls → go to tools node
  ├─ tools node: AgentTools.dispatch(name, args)  →  reads Postgres/Neo4j:
  │     search_apps · search_qvds · search_tables · search_tasks
  │     upstream · downstream · impact · run_cypher(read-only)
  │     └─ tool result appended to messages, loop back to model
  └─ when model returns no tool_calls → final answer
  ▼
{ answer, model, trace }   ← trace = every tool call+result, shown under "Tool trace" in the UI
```

So the agent is **grounded**: it always queries your scanned Postgres/Neo4j data via tools before answering, and never writes (Cypher write keywords are rejected).

## 4. Using the chat window
1. Backend running on `:8000`, frontend on `:5173`.
2. Open `http://localhost:5173` → **Copilot Chat** tab (`/chat`).
3. Type a question (Enter or Send) or click a suggestion chip, e.g.:
   - "What apps use Orders.qvd?"
   - "What will be impacted if Oracle.Customers changes?"
   - "Show upstream dependencies for Sales Dashboard."
4. Expand **Tool trace** under each answer to see which tools ran and their raw DB results.

**Requirement for chat to actually answer:** `LITELLM_MODEL` must be reachable. If `LITELLM_API_KEY`/`LITELLM_API_BASE` are empty and the model can't authenticate, `workflow.py` falls back to retrieval-only (`_fallback`) — it returns raw search context with the message *"LiteLLM unavailable; returning retrieval context only."* instead of a natural-language answer.

## Key point about first-time execution
There is **no auto-population and no auto-refresh** in the current code. The order is always: **boot backend → call `/scan/sync {"mode":"full"}` once → then chat.** On later boots the data is still in the volumes, so you can chat immediately; to refresh you must call `/scan` again with `full` or `delta` (nothing does it automatically yet).
