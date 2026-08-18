# Qlik Environment Setup Reference

How to wire the lineage agent to Qlik Sense Enterprise through the **`/custom`
virtual proxy** using HTTP **header authentication**. This is the standard
method for non-interactive access to the Qlik QRS API from AKS, scripts, and
other automation workloads.

> **Do not** use NTLM. **Do not** connect directly to QRS on port `4242`.
> **Do not** use client certificates. All access is via the virtual proxy over
> HTTPS with authentication headers.

---

## 1. The `/custom` virtual proxy

A Qlik Virtual Proxy named `custom` has been configured for application and API
access. Everything the agent does is routed through it.

| Setting | Value |
|---|---|
| Base URL | `https://<host>/custom` |
| Current validated endpoint | `https://10.221.11.6/custom` |
| Example call | `https://10.221.11.6/custom/qrs/app/full?xrfkey=1234567890abcdef` |

### Required headers (on **every** request)

| Header | Value | Notes |
|---|---|---|
| `X-Qlik-Xrfkey` | `1234567890abcdef` | Must also be repeated as the `xrfkey` query parameter. |
| `X-Qlik-User` | `CORPORATE\srv-qlik` | Format is `DOMAIN\user`. In Python, escape the backslash: `"CORPORATE\\srv-qlik"`. |

### Expected results

- No authentication header → **401 Unauthorized**
- Correct authentication headers → **200 OK**

---

## 2. `.env` values

| Variable | Suggested value | Purpose |
|---|---|---|
| `QLIK_BASE_URL` | `https://10.221.11.6/custom` | Virtual proxy base — **must** include the `/custom` prefix. |
| `QLIK_XRF_KEY` | `1234567890abcdef` | Sent as the `X-Qlik-Xrfkey` header and `xrfkey` query param. |
| `QLIK_USER` | `CORPORATE\srv-qlik` | Sent as the `X-Qlik-User` header. Approved service account. |
| `QLIK_VERIFY_SSL` | `false` | Qlik uses an internal CA; leave `false` unless the root CA is trusted on the agent host. |
| `QLIK_ENGINE_WS_URL` | *(empty)* | Optional full WebSocket override. If empty, derived from `QLIK_BASE_URL` as `wss://<host>/custom<app_path>`. |
| `QLIK_ENGINE_APP_PATH` | `/app` **or** `/sense/app` | Engine WebSocket path. Confirm with your Qlik admin. |
| `QLIK_EXPORT_SCRIPTS` | `false` | Set `true` to dump each load script to disk for debugging. |
| `QLIK_EXPORT_SCRIPTS_DIR` | `D:\tools\FullScriptExport\scripts` | Where dumped scripts go. |
| `QLIK_MAX_CONCURRENT_APPS` | `1` first run → `5`–`10` once stable | Parallel Engine sessions. |

The Engine WebSocket (used to export load scripts) is routed through the same
virtual proxy and authenticates with the same `X-Qlik-User` + `X-Qlik-Xrfkey`
headers — no certificates.

---

## 3. Troubleshooting

| Status | Likely cause |
|---|---|
| **400 Bad Request** | Missing `/custom` in the URL; request routed to a node without the `custom` virtual proxy; malformed URL. |
| **401 Unauthorized** | Missing `X-Qlik-User` or `X-Qlik-Xrfkey` header; invalid user format. |
| **403 Forbidden** | Security rule does not permit access; service account lacks permissions. |

---

## 4. Testing after setup

Run these from the machine that hosts the lineage agent (inside the corporate
network / VPN).

### 4a. curl smoke test (proves the virtual proxy + headers)

```powershell
# Expect 200 with correct headers:
curl.exe -k -i "https://10.221.11.6/custom/qrs/about?xrfkey=1234567890abcdef" `
  -H "X-Qlik-Xrfkey: 1234567890abcdef" `
  -H "X-Qlik-User: CORPORATE\srv-qlik"

# Expect 401 with NO auth headers (negative control):
curl.exe -k -i "https://10.221.11.6/custom/qrs/about?xrfkey=1234567890abcdef"
```

### 4b. Python one-liner against the real client

```powershell
cd backend
python -c "from app.connectors.qrs_client import QrsClient; c=QrsClient('https://10.221.11.6/custom', qlik_user='CORPORATE\\srv-qlik', verify_ssl=False); print(len(c.fetch_apps()), 'apps')"
```

### 4c. Unit tests (mocked — no live Qlik needed)

```powershell
cd backend
python -m pytest tests/test_qrs_connector.py tests/test_engine_connector.py -q
```

### 4d. End-to-end scan via the API

```powershell
# Start the backend, then trigger a synchronous full scan:
curl.exe -X POST http://localhost:8000/scan/sync `
  -H "Content-Type: application/json" `
  -d '{\"mode\":\"full\"}'
```

---

## 5. Reference — how values flow through the code

```
.env
 └── app/config.py::Settings         (loads env into typed settings)
      └── app/dependencies.py         (builds EngineClient / QrsClient)
           ├── QrsClient  ── HTTPS + X-Qlik-User + X-Qlik-Xrfkey ──>
           │        https://10.221.11.6/custom/qrs/app/full?xrfkey=...
           └── EngineClient
                └── EngineSession ── WSS via /custom + headers ──>
                     wss://10.221.11.6/custom/app/<APP_ID>?xrfkey=...
                     → OpenDoc(qNoData=True) → GetScript → CloseDoc → close
```

Developer requirements recap:

- Use the `/custom` virtual proxy prefix.
- Include `X-Qlik-Xrfkey` and `X-Qlik-User` headers on **all** requests.
- Do not use NTLM. Do not connect directly to port `4242`. Use HTTPS through
  the virtual proxy. Use the approved service account.
