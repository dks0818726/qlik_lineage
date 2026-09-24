# Qlik Lineage — Productionization & Security Notes

A future-development reference: how to take this project from "runs on my laptop" to
"a team can rely on it", and the security gaps that must be closed along the way.
Written for someone with no DevOps background — every step says what to do and why.

This document does not need to be actioned immediately. It exists so that whenever
work resumes, the reasoning and the plan are not lost. See `docs/RUNBOOK.md` for
day-to-day operational commands (starting/stopping, scanning, rebuilding).

---

## 1. Where the project stands today

Everything currently runs as a 4-container Docker Compose stack on a single machine
(see RUNBOOK.md section 1): `postgres`, `neo4j`, `backend`, `frontend`. This is a
perfectly reasonable way to run the *whole* production deployment too — it does not
need to be replaced with Kubernetes, Terraform, or any other heavier tooling. What's
missing is everything that assumes "only I use this machine":

- No login/auth on any API endpoint.
- Database ports open to any network reachable from the machine, with default
  passwords (`qlik`/`qlik`, `neo4j`/`password`).
- Everything addressed as `localhost`, so it only works from the machine it runs on.
- No HTTPS.
- No backup of the two data volumes.
- No automatic restart if the machine reboots or a container crashes.

None of these require becoming a DevOps engineer. Each is a small, bounded task.

---

## 2. Security findings (from the 2026-09-15 security review)

A `security-review` agent pass over the `qlik_lineage` repo (excluding the separate
`qmc_user_cleanup` side project) found 6 issues. These are tracked as todos in this
session (`sec-auth-api`, `sec-lockdown-db-ports`, `sec-enforce-tls-verify`,
`sec-remove-cypher-or-lockdown`, `sec-label-untrusted-tool-data`,
`sec-harden-docgen-prompt`) and are re-listed here for a standalone reference.

| # | Severity | Area | File(s) |
|---|----------|------|---------|
| 1 | CRITICAL (10/10) | No authentication on any FastAPI endpoint — anyone who can reach the backend can read all lineage/scripts, trigger scans, delete apps, or run `/graph/cypher` | `backend/app/main.py`, all of `backend/app/api/routers/` |
| 2 | HIGH (9/10) | `/graph/cypher` only blocklists write keywords — does not stop `LOAD CSV` (SSRF) or schema/procedure introspection | `backend/app/api/routers/graph.py`, `backend/app/graph/neo4j_client.py` |
| 3 | HIGH (10/10) | Postgres/Neo4j published on all host interfaces with default credentials — bypasses the API entirely | `docker-compose.yml`, `.env.example` |
| 4 | HIGH (9/10) | TLS verification disabled by default for QRS/Engine connections (`verify=False`, `ssl.CERT_NONE`) — MITM risk | `backend/app/config.py`, `qrs_client.py`, `engine_client.py`, `engine_session.py` |
| 5 | MEDIUM (8/10) | Qlik-sourced strings (app/table/task names) fed into LLM tool-result messages with no "this is untrusted data" framing — prompt-injection surface | `backend/app/agent/workflow.py`, `tools.py`, `backend/app/storage/repository.py` |
| 6 | MEDIUM (8/10) | Documentation-generation prompt interpolates raw script/comment content with no delimiters | `backend/app/docs/generator.py`, `evidence.py`, `condenser.py` |

**Already verified clean, no action needed:** no SQL injection (queries are
parameterized), no command injection, CORS restricted to localhost by default (not
`*`), no raw HTML/`dangerouslySetInnerHTML` rendering in the frontend, and the
context/output caps (150 rows / 24k chars for agent tools, `LIMIT 200` on ad-hoc
Cypher, traversal/impact caps) exist and are enforced.

### Suggested fix order

1. `sec-auth-api` — add auth to the backend first; it unblocks safely locking down
   `/graph/cypher` and the doc-gen endpoint behind real access control.
2. `sec-lockdown-db-ports` and `sec-enforce-tls-verify` — independent of the above,
   can be done in parallel.
3. `sec-remove-cypher-or-lockdown` (needs `sec-auth-api` first).
4. `sec-label-untrusted-tool-data` and `sec-harden-docgen-prompt` (both need
   `sec-auth-api` first).

The full plan with rationale also lives in the session workspace as
`security_remediation_plan.md`. The 6 items above are tracked as `pending` todos in
this session's todo list; query them with the `sql` tool when picking this back up.

---

## 3. Productionization plan (no DevOps background required)

### Step 1 — Get one server that stays on

Ask IT for a small Linux VM with Docker installed (4 vCPU / 16 GB RAM is enough for
a single-team internal tool). This is a routine, well-understood request — no
Kubernetes or cloud architecture needed. Docker Compose (what already exists) is the
correct tool for a single-team deployment.

### Step 2 — Give it a real address

Ask IT for an internal DNS name (e.g. `qlik-lineage.yourcompany.local`) pointing at
the VM, instead of relying on `localhost`. Update:
- Frontend: `VITE_API_BASE_URL` / `VITE_WS_BASE_URL` in `docker-compose.yml`.
- Backend: `CORS_ORIGINS` in `.env` to match the new frontend address.

### Step 3 — Put a reverse proxy in front, with HTTPS

Add one more container — **Caddy** is the easiest option (it fetches HTTPS
certificates automatically from a ~10-line config file). Point it at the backend
(8000) and frontend (5173) ports, and stop publishing those ports (and the database
ports) directly — only the proxy's 443 should be reachable from outside the VM.
This single step also resolves most of the "database ports exposed" security finding
(#3 above) once combined with `sec-lockdown-db-ports`.

### Step 4 — Do the security fixes in section 2

These aren't optional polish once more than one person can reach the deployment —
they are the actual "productionization" work for an app like this. In particular:
- `sec-auth-api` — so only the team can use it.
- `sec-lockdown-db-ports` — critical the moment this isn't just your laptop.

### Step 5 — Back up the data

Two Docker volumes matter: `postgres_data` and `neo4j_data` (see RUNBOOK.md section
1). A daily scheduled job running `docker compose exec postgres pg_dump ...` and
`neo4j-admin dump` to a network share is sufficient — no dedicated backup product
required.

### Step 6 — Survive reboots and crashes

Add `restart: unless-stopped` to each service block in `docker-compose.yml`. One-line
edit per service, no new tooling. Docker will then bring everything back up
automatically after a VM reboot or a container crash.

### Step 7 — (Optional, later) Painless updates

A simple `deploy.sh` running `git pull && docker compose up -d --build` covers most
teams' update needs. A full CI/CD pipeline is a nice-to-have, not a requirement to
go live.

### What is deliberately NOT needed

Kubernetes, Terraform, a dedicated DevOps hire, or a cloud architect. This is a
single-VM Docker Compose application — the ceiling here is "edit a
`docker-compose.yml` and a `Caddyfile`".

---

## 4. Revisit this document when

- Picking the security work back up — start with `sec-auth-api` and validate the
  full backend test suite (67 tests) plus a manual pass of chat/graph/impact/app
  pages before layering the dependent fixes on top (per the plan in section 2).
- A team (not just one person) needs to reach the tool — that's the trigger for
  doing the productionization steps in section 3, not a fixed calendar date.
