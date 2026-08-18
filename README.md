# Qlik Lineage Copilot

Enterprise-grade lineage platform for Qlik Sense apps, scripts, tasks, schedules, QVDs, and source tables — with a realtime AI copilot.

## Stack
- **Backend**: Python 3.12 + FastAPI
- **Lineage graph**: Neo4j 5
- **Metadata store**: PostgreSQL 16
- **Agent**: **LiteLLM** (model-agnostic router) + **LangGraph** (tool-calling state machine) — replaces Google ADK
- **Qlik connectors**: QRS REST + Engine JSON-RPC over WebSocket (read-only), both via the `/custom` virtual proxy using HTTP header authentication (`X-Qlik-User` + `X-Qlik-Xrfkey`)
- **Realtime**: WebSocket `/ws/lineage` + SSE `/events` fan-out hub
- **Frontend**: React 18 + Vite + `vis-network` graph visualization
- **Runtime**: Docker Compose

## Run with Docker
1. `cp .env.example .env` and update credentials.
2. `docker compose up --build`
3. Backend API: <http://localhost:8000> · OpenAPI: <http://localhost:8000/docs>
4. Frontend: <http://localhost:5173>
5. Neo4j Browser: <http://localhost:7474>
6. Authenticate the agent's LLM once (GitHub Copilot device-code flow):
   `docker compose exec backend python copilot_login.py`
   Follow the printed URL + code. The token is cached in `./.copilot-auth`
   (bind-mounted into the container), so it survives restarts and rebuilds.
   Then run `docker compose restart backend`.

## Backend API surface
| Endpoint | Purpose |
|---|---|
| `GET  /health` | Health check |
| `POST /parser/extract` | Parse a Qlik script → lineage deps |
| `POST /lineage/build` | Convert deps → graph edges |
| `POST /scan/run` (background) / `/scan/sync` | Trigger `full` or `delta` scan |
| `GET  /search?q=…&type=App\|QVD\|Table\|Task` | Search nodes |
| `GET  /apps`, `/apps/{id}` | App metadata + script excerpt + lineage |
| `GET  /graph/upstream`, `/downstream`, `/impact`, `/neighborhood` | Graph traversal |
| `POST /graph/cypher` | Run read-only Cypher (write keywords rejected) |
| `POST /agent/ask` | LangGraph + LiteLLM tool-calling Q&A |
| `WS   /ws/lineage` · `GET /events` | Realtime lineage events |

## Capabilities
1. **Metadata collection** — QRS pulls apps, streams, reload tasks, schedules, data connections, owners, composite events. Engine WebSocket retrieves load scripts, table/key models, and app layout.
2. **Script parsing** — multi-line `SQL SELECT`, `LIB CONNECT`, `STORE`, QVD reads (`lib://`, bracketed/quoted), `$(Include=…)`/`$(Must_Include=…)`, `RESIDENT`, block + line comments.
3. **Lineage builder** — emits typed edges `Table→App READS`, `App→QVD WRITES`, `QVD→App READS`, `App→Connection USES`, `Task→App RUNS`, `Task→Task DEPENDS_ON`, `Owner→App OWNS`, `App→Stream BELONGS_TO`, `Task→Schedule SCHEDULED_BY`.
4. **Storage** — PostgreSQL upserts for apps, scripts, qvds, tables, connections, tasks, schedules, owners, streams, `lineage_edges`, `scan_runs`, `change_log`. Neo4j upserts with uniqueness constraints per label.
5. **Incremental updates** — full vs delta mode, hash-based change detection (`script_hash`), per-scan change log, deleted-app cleanup, realtime event emission.
6. **AI Agent (LiteLLM + LangGraph)** — tool catalog: `search_apps`, `search_qvds`, `search_tables`, `search_tasks`, `upstream`, `downstream`, `impact`, `run_cypher`. Graph-based state machine with tool-call → tool-result → model loop.
7. **Frontend** — graph viz, dependency explorer, impact analysis page, app details (script excerpt + neighborhood), Copilot Chat page with tool-trace inspection, live status bar wired to `/ws/lineage`.
8. **Security** — read-only by design. No reload/edit endpoints exposed. Cypher guard blocks `CREATE/MERGE/SET/DELETE/REMOVE/DETACH/DROP/FOREACH/CALL apoc.*` writes.
9. **Scale** — bulk upserts, indexed lineage table, label-unique Neo4j constraints, async event bus, pluggable Engine WebSocket transport for thousands of apps.

## Tests
```powershell
cd backend
python -m unittest discover -s tests -v
```
Covers parser (incl. edge cases for lib://, comments, multi-line SQL, multiple stores, includes), lineage builder (app/task/owner/stream edges), graph upsert query generator, change detector, Cypher read-only guard, QRS + Engine connectors.

## Configuration (.env)
See `.env.example`. Set `LITELLM_MODEL` to any LiteLLM-supported model id (OpenAI, Azure, Anthropic, etc.); set `LITELLM_API_BASE`/`LITELLM_API_KEY` if your gateway needs them. The default is `github_copilot/gpt-4o`, which needs no API key — run `docker compose exec backend python copilot_login.py` once to authenticate via GitHub's device-code flow. If the model is unreachable, `/agent/ask` degrades to a retrieval-only answer instead of failing.

The Qlik Engine connection is a direct mutual-TLS WebSocket to `QLIK_ENGINE_HOST:4747` using `backend/certs/client.pfx` + `root.cer` (it does **not** use the `/custom` virtual proxy). Requires outbound TCP 4747 to the engine host. Large apps are slow to `OpenDoc`, so the Engine uses its own `QLIK_ENGINE_TIMEOUT_SECONDS` (default 300) rather than the QRS timeout.
