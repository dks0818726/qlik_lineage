# Qlik Lineage — Operations Runbook

A step-by-step guide for shutting down, starting up, and rebuilding the full lineage
database. Written to be followed literally — no prior Docker knowledge assumed.

**Every command in this document is run from PowerShell**, and you must first be in the
project folder. Start every session with:

```powershell
cd C:\Users\DKS0818726\Downloads\qlik_lineage
```

If you forget this, commands will fail with "no configuration file provided".

---

## 1. Background: what is actually running

The project runs as four **containers** (small isolated programs) managed by Docker:

| Container  | What it does                                        | Port |
| ---------- | --------------------------------------------------- | ---- |
| `postgres` | Stores raw scan data (apps, scripts, QVDs, tables)  | 5432 |
| `neo4j`    | Stores the lineage **graph** used by the agent      | 7474 / 7687 |
| `backend`  | The FastAPI app — scanner, parser, and the AI agent | 8000 |
| `frontend` | The React web UI                                    | 5173 |

Your data does **not** live inside the containers. It lives in Docker **volumes**
(`qlik_lineage_postgres_data` and `qlik_lineage_neo4j_data`), which survive container
restarts and system reboots. This is why stopping containers is safe.

---

## 2. Shutting down (before closing your laptop)

Run this one command:

```powershell
cd C:\Users\DKS0818726\Downloads\qlik_lineage
docker compose stop -t 60
```

**What this does:** gracefully stops all four containers. Databases flush to disk first,
so nothing is corrupted.

`-t 60` gives each container up to 60 seconds to shut down cleanly. Without it, Docker
waits only 10 seconds and then force-kills. Neo4j often needs longer than 10 seconds once
the graph is populated, and a force-kill mid-write risks a slow recovery on next start.
If you see `Exited (137)` for `neo4j` in the status output, it was force-killed — use the
longer timeout next time.

**What this does NOT do:** it does not delete containers and does not touch your data.
Everything is preserved.

Confirm they stopped:

```powershell
docker compose ps -a
```

You should see all four with status `Exited`.

You can now safely close Docker Desktop and shut down your machine.

> **Do not use `docker compose down -v`.** The `-v` flag deletes the data volumes and
> would wipe your entire lineage database. Plain `docker compose stop` is always the
> safe choice.

---

## 3. Starting back up (next session)

1. Open **Docker Desktop** and wait until the whale icon in the system tray stops
   animating (roughly 30–60 seconds). Docker must be fully running first.

2. Then run:

```powershell
cd C:\Users\DKS0818726\Downloads\qlik_lineage
docker compose up -d
```

`-d` means "detached" — the containers run in the background and give you your prompt
back.

3. Wait about 30 seconds for the databases to finish their health checks, then verify:

```powershell
docker compose ps
```

All four should show `Up`. `postgres` and `neo4j` should also say `(healthy)`.

4. Confirm the backend is answering:

```powershell
curl.exe -s http://localhost:8000/health
```

Expected output: `{"status":"ok"}`

5. Open the UI in your browser: <http://localhost:5173>

> **Note:** use `curl.exe`, not `curl`. In PowerShell, bare `curl` is an alias for a
> different command and will not work correctly here.

### If a container will not start

Look at its logs — the error is almost always printed there:

```powershell
docker compose logs --tail 50 backend
```

Replace `backend` with whichever container is failing.

---

## 4. Rebuilding after a code change

If you (or I) change Python code in `backend/`, the running container still holds the
**old** copy. You must rebuild the image:

```powershell
cd C:\Users\DKS0818726\Downloads\qlik_lineage
docker compose up -d --build backend
```

Takes 1–3 minutes. This is a very common cause of "my fix didn't work" — the code
changed on disk but the container was never rebuilt.

---

## 5. Rebuilding the entire lineage database from scratch

This is the main task waiting for you. The parser was significantly fixed (multi-part
BigQuery table names, JOINs, `REM` comments, and Qlik `$(variable)` expansion), but the
database still needs to be repopulated for those fixes to take effect.

**The databases have already been wiped for you.** Postgres is truncated and Neo4j is
empty, so you can skip straight to Step 5.3 — but run the safety check first.

### 5.1 Why a wipe is required

The scanner is incremental: `_process_app` compares each app's `script_hash` and skips
apps whose script has not changed. After a parser change the scripts are identical, so a
plain re-scan would skip everything and produce no new lineage. The tables must be empty
to force a full re-parse.

### 5.2 Wipe the databases (only if the check in 5.3 shows non-zero)

```powershell
docker compose exec -T postgres psql -U qlik -d qlik_lineage -c "TRUNCATE apps, scripts, qvds, source_tables, lineage_edges, tasks, connections, streams, owners, schedules, change_log, scan_runs RESTART IDENTITY CASCADE;"
```

```powershell
docker compose exec -T neo4j cypher-shell -u neo4j -p password "MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS;"
```

This is safe: all of this data is **derived**. The scan re-fetches everything from Qlik.

### 5.3 Safety check — confirm both are empty

```powershell
docker compose exec -T postgres psql -U qlik -d qlik_lineage -c "SELECT count(*) FROM apps;"
docker compose exec -T neo4j cypher-shell -u neo4j -p password "MATCH (n) RETURN count(n);"
```

Both must print `0`. If not, run Step 5.2.

### 5.4 Start the full scan

```powershell
$body = @{ mode = 'full' } | ConvertTo-Json
Invoke-RestMethod -Uri http://localhost:8000/scan/run -Method Post -ContentType 'application/json' -Body $body
```

The command returns immediately with a scan id. The scan itself runs in the background
and takes **roughly 18–30 minutes** for all 1,760 apps.

> Use `Invoke-RestMethod`, not `curl.exe`, for any command that sends JSON. PowerShell
> mangles the quoting inside `-d '{\"mode\":\"full\"}'` and the server rejects it with a
> `json_invalid` error. `curl.exe` is still fine for simple GETs like `/health`.

You can close the PowerShell window — the scan runs inside the container, not in your
terminal. Just do not stop Docker.

### 5.5 Monitor progress

Check status (re-run every few minutes):

```powershell
docker compose exec -T postgres psql -U qlik -d qlik_lineage -c "SELECT scan_id, mode, status, started_at, completed_at, round(extract(epoch from (coalesce(completed_at,now())-started_at))/60.0,1) AS mins, stats FROM scan_runs ORDER BY started_at DESC LIMIT 3;"
```

`status` moves from `running` to `succeeded`. When `completed_at` is filled in, it's done.
The `stats` column holds the summary counts (`apps_seen`, `apps_changed`,
`edges_upserted`, `new_qvds`).

> The primary key is `scan_id` (text), not `id`, and the counts live inside the `stats`
> JSON column — there are no `apps_seen` / `apps_changed` columns to select directly.

To watch live activity:

```powershell
docker compose logs -f --tail 30 backend
```

Press `Ctrl+C` to stop watching. This only stops the log display, **not** the scan.

---

## 6. Verifying the results

Run these after the scan reaches `success`.

### A. Core counts — did the parser fixes land?

```powershell
docker compose exec -T postgres psql -U qlik -d qlik_lineage -c "SELECT (SELECT count(*) FROM apps) apps, (SELECT count(*) FROM lineage_edges) edges, (SELECT count(*) FROM qvds) qvds, (SELECT count(*) FROM source_tables) tables;"
```

| Metric | Previous scan | Actual (2026-08-17) | Why it changed |
| ------ | ------------- | ------------------- | -------------- |
| apps   | 1,760 | 1,759 | unchanged |
| edges  | 28,032 | 27,578 | see note below |
| qvds   | 2,379 | **2,518** | `STORE INTO $(vDir)/x.qvd` previously matched nothing at all |
| tables | 646 | 656 | variable expansion merged duplicates |

> **On the edge count:** `lineage_edges` has a UNIQUE constraint on
> `(source_type, source_id, relation, target_type, target_id)`, so it stores *distinct*
> edges. The parser emits ~43,000 raw dependency records, but the same QVD loaded twice
> in one script collapses to a single edge. A slightly *lower* distinct count than the
> previous scan is expected and correct: canonicalisation and variable expansion **merge**
> nodes that used to be separate, so the graph gets denser and better connected rather
> than larger. The real quality metric is test E below, not the raw edge count.

### B. Qlik variable expansion worked

```powershell
docker compose exec -T postgres psql -U qlik -d qlik_lineage -c "SELECT count(*) FILTER (WHERE qvd_path LIKE '%$(%') AS unresolved, count(*) AS total FROM qvds;"
```

Expected: about **681 of 2,518 (27%)**, down from 728 of 2,379 (30.6%).

The remaining unresolved ones are `$(pQvdDirectory)`-style **subroutine parameters**.
Their values are supplied at call time, so they cannot be resolved by reading the script
alone. This is a known and accepted limit.

### C. BigQuery multi-part table names are intact

```powershell
docker compose exec -T postgres psql -U qlik -d qlik_lineage -c "SELECT name FROM source_tables WHERE name LIKE '%.%.%' LIMIT 10;"
```

Expected: complete three-part names such as
`p-wardar-381480765.reporting.fa_cmm_complete_base`.

Previously these were truncated to `p-wardar-381480765.reporting`, silently losing the
table name in 495 cases.

### D. The graph is populated

```powershell
docker compose exec -T neo4j cypher-shell -u neo4j -p password "MATCH (n) RETURN labels(n)[0] AS label, count(*) ORDER BY count(*) DESC;"
```

Expected labels: `QVD`, `App`, `Task`, `Table`, `Connection`, `Stream`, `Owner`.

### E. QVD bridging — the most important lineage test

```powershell
docker compose exec -T neo4j cypher-shell -u neo4j -p password "MATCH (w:App)-[:WRITES]->(q:QVD)-[:READS]->(r:App) RETURN count(DISTINCT q) AS bridging_qvds, count(*) AS links;"
```

This finds QVDs that connect a producing app to a consuming app — the backbone of
end-to-end lineage, and the single best measure of whether the parser fixes worked.

| | Before fixes | Actual (2026-08-17) |
| --- | --- | --- |
| Bridging QVDs | 664 | **731** (+10%) |
| End-to-end links | 32,395 | **39,066** (+20.6%) |

### F. The specific bug that produced a wrong answer

```powershell
docker compose exec -T neo4j cypher-shell -u neo4j -p password "MATCH (a:App)-[:WRITES]->(q:QVD {id:'e_date_dim.qvd'}) RETURN count(a) AS writers;"
```

Expected: roughly **15**. Confirmed on 2026-08-17: 15 writers and 468 readers (up from
438 readers). Previously the agent landed on a `$(var)`-prefixed duplicate of this node
and confidently answered *"written by no app"* — the failure that motivated the
variable-expansion work.

### Note: why Neo4j shows more `Table` nodes than Postgres

Neo4j shows ~1,358 `Table` nodes while Postgres `source_tables` has only 656. This is
**not** a bug. Two different things share the `:Table` label:

- `(Table)-[:READS]->(App)` — **656** external source tables, matching Postgres exactly.
  Node ids are qualified with the connection name, e.g.
  `GCP - Customer-Mkt-Data.customer-mkt-data.u_dsg_pricing.iss_top_progs`.
- `(App)-[:DEPENDS_ON]->(Table)` — **702** Qlik **resident / in-memory** tables such as
  `TMP_LW_TY` or `WEEK_DIM_TMP`, produced by `LOAD ... RESIDENT <name>`.

The two sets are currently disjoint (656 + 702 = 1,358), so no external table is being
conflated with a temp table. See section 10 for a minor known issue with these.

### G. End-to-end agent test

```powershell
$body = @{ question = 'Which QVD is read by the most apps, and how many apps write it?' } | ConvertTo-Json
$r = Invoke-RestMethod -Uri http://localhost:8000/agent/ask -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 180
$r.answer
```

Verified working on 2026-08-17. The agent issued two correct Cypher queries and answered:
*"`lib://qlikstorage/ebir/extract/dim/e_date_dim.qvd`, read by 468 apps, written by 15
apps"* — exactly matching the direct database queries in tests E and F.

Use `$r | ConvertTo-Json -Depth 4` to see the full reasoning trace.

### H. Parser unit tests (fast, no scan needed)

```powershell
docker compose exec -T backend pip install -q pytest
docker compose exec -T backend python -m pytest tests -q
```

Expected: `42 passed`. These lock in every parser bug that was fixed.

`pytest` is not in `requirements.txt`, so the `pip install` line must be re-run after
each rebuild.

---

## 7. Showcase prompts for the agent

These prompts are ordered from simple to advanced and are designed to demonstrate the
full range of what the agent can do. Every one has been run against the live database —
the "verified" notes record what it actually returned on 2026-08-17.

### How to ask a question

Save this helper once per PowerShell session so you don't retype it:

```powershell
function Ask($q) {
  $body = @{ question = $q } | ConvertTo-Json
  $global:r = Invoke-RestMethod -Uri http://localhost:8000/agent/ask -Method Post `
        -ContentType 'application/json' -Body $body -TimeoutSec 240
  $global:r.answer
}
```

Then simply:

```powershell
Ask "Which QVD is read by the most apps?"
```

To see which tools the agent chose and the Cypher it wrote, inspect `$r` afterwards
(the helper stores the last full response there):

```powershell
$r.trace | ConvertTo-Json -Depth 4

# just the Cypher statements it ran
$r.trace | Where-Object { $_.step -eq 'tool.result' } | ForEach-Object { $_.args.statement }
```

You can also use the web UI at <http://localhost:5173>, which is friendlier for reading
long answers.

### What the agent has to work with

It has nine tools: `search_apps`, `search_qvds`, `search_tables`, `search_tasks`,
`qvd_variants`, `upstream`, `downstream`, `impact`, and `run_cypher` (free-form Cypher
against Neo4j). The graph currently holds:

| Node type | Count |
| --------- | ----- |
| QVD | 2,518 |
| App | 1,759 |
| Table | 1,358 (656 external + 702 resident) |
| Task | 1,016 |
| Connection | 78 |
| Owner | 78 |
| Stream | 24 |

---

### Tier 1 — Orientation

**1. Inventory overview**

> Give me a summary of the lineage graph: how many apps, QVDs, source tables, tasks and
> connections are tracked, and roughly how many lineage relationships connect them.

*Showcases:* schema awareness and aggregation.
*Verified:* returns the counts in the table above.

**2. Most critical shared data asset**

> Which QVD is read by the most apps, and how many apps write it?

*Showcases:* correct edge-direction reasoning — the agent must know lineage runs
`QVD -[:READS]-> App` and `App -[:WRITES]-> QVD`, which is the reverse of how the
question is phrased in English.
*Verified:* `lib://qlikstorage/ebir/extract/dim/e_date_dim.qvd` — 468 readers, 15 writers.
This exactly matches a direct database query.

**3. Top shared assets**

> List the 10 most widely reused QVDs by number of consuming apps, and for each show how
> many apps produce it.

*Showcases:* multi-metric ranking in one query.
*Verified:* `e_date_dim.qvd` (468), `e_product_dim.qvd` (293), `t_date_dim.qvd` (290),
`e_location_dim.qvd` (264).

---

### Tier 2 — Impact analysis (the flagship capability)

**4. Failure blast radius**

> If the QVD `e_date_dim.qvd` failed to refresh tonight, which apps would be impacted?
> Give me the count and name the top 5 apps.

*Showcases:* the `qvd_variants` tool automatically merging the eight fragmented copies of
this file, then `impact` and `search_apps` for readable names.
*Verified:* **614 distinct apps** — matching a direct database query exactly. A plain
filename is enough; you no longer need the full path.

**5. Change-safety check before editing an app**

> I need to modify the app "ATH - Sales Analysis FY24-FY25". What does it read, what does
> it write, and which downstream apps consume what it produces? Summarise the risk of
> changing it.

*Showcases:* `search_apps` → `upstream` → `downstream` chained together, plus
qualitative judgement grounded in real edges. This app has 96 incoming dependencies, the
most in the estate.

**6. Full upstream provenance**

> Trace the complete upstream lineage of the app "ERA - Executive Mashup (QA)". Show the
> chain from original database tables through every intermediate QVD to the app itself.

*Showcases:* multi-hop traversal across `Table → App → QVD → App` — the end-to-end
lineage that the parser fixes were built to make possible.

**7. Reverse root-cause**

> A dashboard is showing stale dates. Starting from the app "AD Starr Reporting Hub",
> walk backwards and list every QVD and source table it depends on, so I can find which
> upstream job failed.

*Showcases:* practical incident-response reasoning, not just graph dumping.

---

### Tier 3 — Governance and hygiene

**8. Orphaned inputs**

> How many QVDs are read by at least one app but never written by any app? Show me a few
> examples. What does that imply about our lineage coverage?

*Showcases:* negative pattern matching, and the agent interpreting a real audit finding.
*Verified:* **1,190** orphan QVDs — produced outside the scanned estate, or by apps whose
paths still contain unresolved variables.

**9. Unscheduled apps**

> How many apps have no reload task attached? List 10 of them. Which of those still
> write QVDs that other apps depend on?

*Showcases:* combining a negative pattern with a downstream check to find real risk —
an unscheduled app that other apps depend on is a live hazard.
*Verified:* **972** apps have no `Task`.

**10. Dead assets**

> Find QVDs that are written by an app but never read by any app. List 15. These may be
> candidates for cleanup.

*Showcases:* the inverse gap analysis — wasted storage and dead pipeline branches.

**11. Single points of failure**

> Which 10 apps would cause the widest disruption if they failed, measured by how many
> other apps depend on the QVDs they produce?

*Showcases:* two-hop aggregation ranking systemic criticality, with automatic UUID-to-name
resolution.
*Verified:* the "ATH / EBIR - Extract Dimensions" apps at **670 distinct consumers each** —
confirmed against a direct database query.

**12. Ownership accountability**

> Group apps by owner and show the 10 owners who control the most apps. For the top
> owner, list the QVDs their apps produce that other owners' apps consume.

*Showcases:* joining the `Owner` dimension to lineage — cross-team dependency mapping.

---

### Tier 4 — Source systems and migration planning

**13. Database footprint**

> Which external source tables feed the most Qlik apps? Show the top 15 with the number
> of apps consuming each, and indicate which connection each comes from.

*Showcases:* `Table -[:READS]-> App` and `Table -[:BELONGS_TO]-> Connection`.
*Verified:* 656 external source tables across 78 connections.

**14. BigQuery migration scope**

> List the BigQuery source tables we depend on — those with fully-qualified
> `project.dataset.table` names. How many are there, and which apps would break if the
> `reporting` dataset were renamed?

*Showcases:* the multi-part table-name fix directly. Before that fix these names were
truncated to `project.dataset`, losing the table entirely in 495 cases.
*Verified:* 347 three-part table names are stored intact.

**15. Connection decommissioning**

> If we retired the connection with the most dependent tables, how many apps and tables
> would be affected? Name the connection and quantify the blast radius.

*Showcases:* impact analysis on the infrastructure layer rather than the data layer.

---

### Tier 5 — Advanced reasoning

**16. Pipeline layer detection**

> Our QVD paths follow an `extract` → `transform` → published pattern. Count the QVDs in
> each layer and show me one complete example flowing from an extract QVD through a
> transform QVD into a consuming app.

*Showcases:* inferring architectural convention from path naming and validating it
against real edges.

**17. Duplicate work detection**

> Find cases where two or more different apps write to QVDs with the same filename in
> different folders. These may be duplicated pipelines. Show the 10 clearest examples.

*Showcases:* string manipulation in Cypher plus genuine consolidation insight.

**18. Fragmentation audit (a known data-quality issue)**

> The same physical QVD sometimes appears under several paths because of unresolved Qlik
> variables. Show me every path variant of `e_date_dim.qvd` with its reader count, and the
> combined total of distinct apps.

*Showcases:* the `qvd_variants` tool, and the agent being transparent about a limitation
in its own data.
*Verified:* 8 variants — `lib://qlikstorage/...` (468), `$(vtargetserver)` (122),
`$(vserver)` (29), `$(vpath)` (12), `$(vqlikstoragedirectory)` (5) and others, combining to
**614 distinct reader apps**. See section 10.

**19. Chain depth**

> What is the longest dependency chain in the environment — the deepest path from a
> source table through apps and QVDs to a final consuming app? Show the path and explain
> why long chains are risky.

*Showcases:* variable-length path traversal plus interpretation.
*Tip:* if this times out, add "limit the search to paths of at most 6 hops".

**20. Executive briefing**

> Act as a data governance lead. Based on the lineage graph, write a short briefing for
> management covering: our most critical shared data assets, the biggest single points of
> failure, and the top three data-quality risks you can evidence. Cite specific numbers.

*Showcases:* synthesis across many queries into a narrative — the closest thing to the
agent's full potential in a single prompt.

---

### Getting good answers

The agent is designed so that **plain, natural questions work**. You should not need to
know Cypher or the graph schema. Ask the way you would ask a colleague:

> *"If e_date_dim.qvd failed tonight, what breaks?"*
> *"Which apps are most dangerous to change?"*
> *"What data do we load from BigQuery?"*

Three optional habits that improve any answer:

**Ask for counts before lists.** "How many..." then "show me the top 10" reads better and
returns faster than requesting everything at once. Very large listings are automatically
capped, and the agent will tell you when it is showing a sample.

**Ask it to show its working.** Adding *"show me the query you used"* prints the Cypher so
you can verify the number yourself. Every figure in this section was cross-checked against
a direct database query.

**Treat a zero with suspicion.** If the agent says "none found" for something you believe
exists, ask it to try again and check the relationship direction. A reversed arrow returns
an empty result rather than an error, so it can look like a genuine finding.

#### If an answer looks wrong

Ask a follow-up in the same conversation — *"are you sure? check for path variants"* or
*"count distinct nodes, not rows"*. The agent has guidance for these cases built into its
system prompt, and a nudge is usually enough.

Historical note: before the 2026-08-17 fixes, all three of the following failed. They are
kept here as regression tests — if any of them ever regresses, the agent has a real bug:

| Natural question | Old answer | Correct answer |
| ---------------- | ---------- | -------------- |
| *"If e_date_dim.qvd failed, which apps are impacted?"* | 30 apps | **614** |
| *"How many QVDs are read but never written?"* | 6,000, then 0 | **1,190** |
| *"Which apps would cause the widest disruption?"* | "No apps were found" | **670 consumers each** |

---

## 8. Troubleshooting

### The agent says a result was truncated, or shows only a sample

This is expected and safe. The model has a 64,000-token context window, and an unbounded
query can far exceed it — one early failure measured 185,906 tokens and returned
`LLM unavailable (prompt token count of 185906 exceeds the limit of 64000)`.

Three protections now prevent that:

- Ad-hoc Cypher without a `LIMIT` is automatically capped at 200 rows (aggregate-only
  queries such as `RETURN count(DISTINCT x)` are left alone, so totals stay exact).
- Graph traversals (`upstream`, `downstream`) return at most 60 paths; `impact` returns
  exact per-type totals alongside a 200-node sample, so the *count* is right even when the
  list is trimmed.
- Any oversized tool result is truncated before reaching the model, with an explicit
  `truncated: true` marker telling it to re-query with an aggregate rather than
  under-count.

If you need the complete set rather than a sample, ask for a count first
(*"how many are there?"*), then narrow the listing (*"show me the ones in the EBIR
stream"*). To extract bulk data, query Postgres or Neo4j directly using the commands in
section 6 — the agent is built for analysis, not bulk export.

### The agent returns a generic fallback instead of a real answer

The GitHub Copilot token has likely expired. Re-authenticate:

```powershell
docker compose exec -T backend python copilot_login.py
```

It prints a URL and a code — open the URL in your browser, enter the code, and the token
is saved to `.copilot-auth\` on your machine, which is mounted into the container. You do
not need to rebuild afterwards.

### Edge count comes out well below ~27,000

The container may be running a stale image. Rebuild and re-scan:

```powershell
docker compose up -d --build backend
```

### "Cannot connect to the Docker daemon"

Docker Desktop is not running, or has not finished starting. Open it and wait for the
tray icon to settle.

### Scan fails with a connection or timeout error

Verify the Qlik Engine is reachable on port 4747:

```powershell
Test-NetConnection ms16-p-0295.dcsg.com -Port 4747
```

`TcpTestSucceeded : True` is required. If it is `False`, the firewall rule has lapsed and
must be reopened — this was the original blocker for this project.

---

## 9. Disk usage — what grows and what does not

A previously raised concern, answered with measurements:

| Item | Size | Behaviour |
| ---- | ---- | --------- |
| Neo4j `/data/transactions` | ~515 MB | **Fixed.** Preallocated transaction logs, reserved up front and reused forever. Does not grow with scans. |
| Neo4j `/data/databases` | ~2 MB empty | Grows with the graph, but modestly. |
| Postgres volume | ~82 MB empty | `TRUNCATE` returns disk to the OS, so it does not creep upward. |
| Docker **build cache** | grows unbounded | **This is the only real growth risk** — dead layers accumulate with every rebuild. |

Clear the build cache whenever it gets large (1.72 GB was reclaimed recently):

```powershell
docker system df          # check current usage
docker builder prune -af  # safe: only deletes cached build layers, never data
```

`docker builder prune` never touches volumes or databases. The next build is simply a bit
slower because it starts from scratch.

---

## 10. Known open items

### Open: QVD nodes fragmented by unresolved variables

681 of 2,518 QVD nodes (27%) still contain an unresolved `$(variable)` in their path. The
parser expands variables a script defines itself, but when a variable is defined elsewhere —
an external `$(Include)` file, a subroutine parameter, or the Qlik environment — it cannot
be resolved from the script text alone.

One physical file therefore splits across several nodes. For `e_date_dim.qvd`:

| Node id | Readers |
| ------- | ------- |
| `lib://qlikstorage/ebir/extract/dim/e_date_dim.qvd` | 468 |
| `lib://$(vtargetserver)/ebir/extract/dim/e_date_dim.qvd` | 122 |
| `lib://$(vserver)/ebir/extract/dim/e_date_dim.qvd` | 29 |
| `lib://$(vpath)/ebir/extract/dim/e_date_dim.qvd` | 12 |
| `lib://$(vqlikstoragedirectory)/ebir/extract/dim/e_date_dim.qvd` | 5 |
| plus 3 more variants | |
| **Combined distinct readers** | **614** |

**This is now handled at query time.** The `qvd_variants` tool matches on filename suffix
and reports the combined total, and the system prompt instructs the agent to call it first
whenever a QVD is named. Asking *"if e_date_dim.qvd failed, what breaks?"* correctly
returns 614; before the fix it returned 30.

The underlying data is still fragmented, so **graph-level metrics remain understated** —
per-node reader counts, the bridging-QVD figure in test E, and any hand-written Cypher that
targets a single id.

#### Why not just substitute the most common value?

The obvious fix is to build a corpus-wide dictionary of variable values and use the most
common one as a fallback. Measuring the actual definitions across all 1,759 scripts shows
why that is unsafe:

| Variable | Definitions found | Distinct values | Most common |
| -------- | ----------------- | --------------- | ----------- |
| `vQlikStorageDirectory` | 327 | 5 | `QlikStorage` (88.1%) |
| `vQvdDirectory` | 1,310 | 45 | `''` empty (28.6%) |
| `vTargetServer` | 17 | 2 | `QlikStorage` (70.6%) |
| `vServer` | 1 | 1 | `QlikStorage` |
| `vPath` | 0 | — | — |

Four separate problems:

1. **It would silently merge environments.** `vQlikStorageDirectory` resolves to
   `QlikStorage` (88.1%), `QlikStorage - PROD` (6.1%) and `QlikStorage - DEV` (0.9%).
   Substituting the most common value would rewrite genuine DEV and PROD paths into the
   default namespace, inventing edges that do not exist — a DEV app would appear to write
   the production QVD that 468 apps read. **False lineage is worse than missing lineage:**
   a missing edge is a visible gap, whereas a false edge silently corrupts every impact
   analysis built on it, in both directions (false alarms *and* missed real dependencies).
2. **The sample is often too small to justify a guess.** `vServer` appears in exactly one
   script yet prefixes dozens of QVD nodes, so a global default would be extrapolated from
   a single observation.
3. **Some variables have no in-script definition at all.** `vPath` is never defined in any
   scanned script, so there is nothing to learn from.
4. **Some are genuinely per-app.** `vQvdDirectory` takes 45 distinct values and its most
   common value is the empty string. There is no meaningful global default.

#### Better fixes, in order of value

1. **Resolve `$(Include)` files during scanning.** These variables are almost certainly set
   in shared include scripts. Reading them gives the *real* value per app rather than a
   guess, fixing the data instead of masking it. Currently only one include file is
   referenced in the scanned estate, so the scanner likely needs filesystem or `lib://`
   access to fetch them — worth investigating first.
2. **Per-variable allow-list.** Substitute a global default only for variables that resolve
   to exactly one value across the whole corpus, and only where no environment-suffixed
   variant (`- PROD`, `- DEV`) exists anywhere. Safe but narrow.
3. **Leave the data as-is and keep resolving at query time** (the current approach).
   Zero risk of false lineage; graph metrics stay understated.

Option 1 is the correct long-term fix. Options 2 and 3 are safe stop-gaps. A naive
most-common-value merge should be avoided.
### Resolved: QRS owner enrichment

The `/qrs/user/full` timeout that previously skipped owner data did **not** recur on the
2026-08-17 scan. All 1,759 apps have an `OWNS` edge (78 distinct owners). No action
needed unless it reappears.

### Minor: resident table names are not case-normalised

External source tables go through `canonical_table()` (lowercased), but resident/temp
table names in `(App)-[:DEPENDS_ON]->(Table)` keep their original casing. As a result 24
resident table names are split across 51 nodes purely by case (e.g. `TMP_LW_TY` vs
`tmp_lw_ty`), even though Qlik treats table names case-insensitively.

Impact is low — resident tables are internal scratch tables, not real data sources, and
they do not affect QVD or source-table lineage. Worth fixing only if you plan to query
in-app transformation chains. The fix is to apply `canonical_table()` to
`dep.resident_table` in `backend/app/lineage/builder.py` (line ~37).

---

## 11. Keeping the data current

Developers change load scripts, new apps appear, old ones get deleted. This
section explains how the lineage database keeps up.

### 11.1 How change is detected

A scan compares Qlik against what is already stored, at three levels:

| Level | Compares | Catches |
| ----- | -------- | ------- |
| App list | Qlik's app IDs vs stored app IDs | Apps **added** or **deleted** |
| `modifiedDate` | Qlik's timestamp vs stored one | Which apps are **worth re-opening** |
| `script_hash` | SHA-256 of script text vs stored hash | Whether the script **actually** changed |

The third level matters. Qlik bumps `modifiedDate` whenever an app is opened or
reloaded, even if nobody edited a line. The hash is the real test — if it
matches, the app is skipped with zero writes. This is why a delta scan over an
unchanged estate is fast and harmless.

### 11.2 What the scanner does in each case

| Situation | Action |
| --------- | ------ |
| New app | Fetch script, parse, insert nodes and edges |
| Script changed | Delete that app's old script-derived edges, re-parse, insert fresh |
| Script unchanged | Skip entirely (no writes) |
| App deleted in Qlik | Delete the app plus all its edges, from both databases |
| QVD/table left with no edges | Pruned, so counts reflect what is genuinely in use |

Note the "script changed" row: only **script-derived** edges (`READS`, `WRITES`,
`USES`, `DEPENDS_ON`) are deleted. Schedule (`RUNS`), owner (`OWNS`) and stream
(`BELONGS_TO`) links come from the Qlik API, not the script, so they are left
alone. If they were deleted here and the API call failed later in the same scan,
that lineage would be lost with nothing to restore it.

### 11.3 Delta vs full

```powershell
# Delta - only apps whose modifiedDate moved. Minutes.
Invoke-RestMethod -Uri http://localhost:8000/scan/run -Method Post `
  -ContentType application/json -Body (@{mode='delta'} | ConvertTo-Json)

# Full - re-reads every app. ~18 minutes for 1,759 apps.
Invoke-RestMethod -Uri http://localhost:8000/scan/run -Method Post `
  -ContentType application/json -Body (@{mode='full'} | ConvertTo-Json)
```

| Mode | Use when |
| ---- | -------- |
| `delta` | Routine freshness. Safe to run often. |
| `full` | Weekly reconciliation, or after the scanner has been offline a while. |
| Wipe + `full` | **After changing the parser.** See section 5. |

That last row is the one people get wrong. A parser change does not alter any
script, so every hash still matches and both `delta` and `full` will skip every
app. The improved parsing logic never runs. Only a wipe forces a true re-parse.

### 11.4 Running it automatically

Set the interval in `.env`, then restart the backend:

```
SCAN_INTERVAL_SECONDS=3600     # hourly. 21600 = every 6h, 86400 = nightly, 0 = off
```

```powershell
docker compose up -d backend
docker compose logs backend | Select-String "scheduler"
```

A background thread runs a delta scan on that cadence. It survives individual
scan failures — one bad run does not stop the schedule. Suggested starting
point: **delta every 6 hours, full scan weekly**.

### 11.5 The mass-deletion safety valve

Deletions are real deletions, which makes a bad Qlik API response dangerous: a
timeout or paging glitch that returns a partial app list would otherwise look
like hundreds of apps being deleted at once.

So if more than **10 apps or 20% of the estate** disappear in a single scan, the
scanner refuses to delete anything, logs an error, and records
`deletions_skipped` in the scan stats. Real bulk deletions are picked up on the
next scan once the app list is stable.

To check whether this fired:

```powershell
docker compose exec -T postgres psql -U qlik -d qlik_lineage -c `
  "SELECT scan_id, status, stats FROM scan_runs ORDER BY completed_at DESC LIMIT 3;"
```

Look for `deletions_skipped` in the `stats` column. If it appears, investigate
the Qlik connection before trusting that scan.

### 11.6 Confirming a scan behaved

Compare counts before and after. Stable or slowly growing numbers are healthy; a
sudden collapse means something went wrong.

```powershell
docker compose exec -T postgres psql -U qlik -d qlik_lineage -t -c `
  "SELECT 'apps='||(SELECT count(*) FROM apps)||' qvds='||(SELECT count(*) FROM qvds)||' tables='||(SELECT count(*) FROM source_tables)||' edges='||(SELECT count(*) FROM lineage_edges);"
```

Baseline after the last verified full scan:

```
apps=1759 qvds=2518 tables=656 edges=27578
```

---

## 12. Quick reference

| Task | Command |
| ---- | ------- |
| Stop everything (safe) | `docker compose stop -t 60` |
| Start everything | `docker compose up -d` |
| Check status | `docker compose ps` |
| View logs | `docker compose logs -f --tail 50 backend` |
| Rebuild after code change | `docker compose up -d --build backend` |
| Health check | `curl.exe -s http://localhost:8000/health` |
| Web UI | <http://localhost:5173> |
| Ask the agent a question | see section 7 for the `Ask` helper and 20 example prompts |
| Refresh lineage (delta) | see section 11.3 — safe to run often |
| Automatic refresh | set `SCAN_INTERVAL_SECONDS` in `.env`, see section 11.4 |
| Neo4j browser | <http://localhost:7474> (user `neo4j`, password `password`) |
| Reclaim disk | `docker builder prune -af` |
| **Never run** | `docker compose down -v` — deletes all data |
