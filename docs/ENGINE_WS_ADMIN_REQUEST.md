# Engine WebSocket — Admin Resolution Request

Request for the platform / Qlik admin team to enable **Qlik Engine WebSocket**
routing on the `custom` virtual proxy. Companion reproduction script:
[`backend/ws_handshake_test.py`](../backend/ws_handshake_test.py).

---

**Subject: Enable Engine WebSocket routing on the `custom` virtual proxy (header-auth service account)**

## Context
Our service account `CORPORATE\srv-qlik` accesses Qlik Sense read-only through the
`/custom` virtual proxy using **HTTP header authentication** (`X-Qlik-User` +
`X-Qlik-Xrfkey`). QRS REST works, but the **Qlik Engine WebSocket** connection
through the same proxy is rejected at the proxy layer, so we cannot retrieve app
load scripts (needed for data lineage).

## Evidence
Reproducible with the attached `ws_handshake_test.py`, run from our backend host:

| Request | Expected | Actual |
|---|---|---|
| `GET https://10.221.11.6/custom/qrs/about` | 200 | **HTTP 200** ✅ |
| `GET https://10.221.11.6/custom/qrs/app/full` | 200 | **HTTP 200** ✅ |
| WS `wss://10.221.11.6/custom/app/<appId>?xrfkey=…` | 101 | **HTTP 403 Forbidden** ❌ |
| WS `wss://10.221.11.6/custom/sense/app/<appId>?xrfkey=…` | 101 | **HTTP 500 "Unable to route the WebSockets request"** ❌ |

A working handshake must return **`HTTP/1.1 101 Switching Protocols`**. The 403 is
independent of xrfkey / Origin / appId, and REST on the same proxy succeeds — so
this is a virtual-proxy **WebSocket configuration** issue, not an auth or network
issue.

## Requested changes (QMC → Virtual proxies → `custom`)
1. **WebSocket origin white list** — add the origin(s) our client uses:
   `10.221.11.6` (and the backend host/IP if different). An empty or mismatched
   white list produces exactly this 403 on WS upgrade.
2. **Load balancing** — ensure the **Engine (QIX) node(s)** are listed as
   load-balancing nodes for this proxy. The `/sense/app` "Unable to route the
   WebSockets request" 500 indicates the proxy currently has no engine target to
   route the socket to.
3. Confirm the proxy **allows WebSocket upgrades** for header-authenticated
   sessions (some header-auth proxies are locked to REST only).

## How to verify the fix
Re-run `ws_handshake_test.py`. **Success = STEP 2 returns
`HTTP/1.1 101 Switching Protocols`** for `wss://…/custom/app/<appId>`.

---

## How to run the reproduction script
```powershell
cd backend
python ws_handshake_test.py
```
STEP 1 proves REST header-auth works (HTTP 200). STEP 2 performs the raw Engine
WebSocket upgrade and prints the proxy's verbatim HTTP status line. The app IDs
used are real GUIDs fetched live from the server, so the output is a valid
reproduction case.

## Once fixed
When STEP 2 shows `101 Switching Protocols`, re-run the full metadata scan to
populate Postgres + Neo4j:
```powershell
curl -X POST http://localhost:8000/scan/sync -H "Content-Type: application/json" -d '{"mode":"full"}'
```
