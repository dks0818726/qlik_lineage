"""
Qlik Engine WebSocket handshake test  (client-certificate / mutual-TLS variant)
================================================================================

Purpose
-------
Reproduce the connection strategy of the reference ``loadScriptExport.js`` (which
uses enigma.js + the ``ws`` library) but in Python, so we can prove that a direct
Qlik ENGINE WebSocket connection succeeds when authenticated with a **client
certificate (mutual TLS)** instead of going through the ``/custom`` virtual proxy
with header-only auth.

Reference (loadScriptExport.js)::

    url: wss://ms16-p-0295.dcsg.com:4747/sense/app/<APP_ID>
    createSocket: new WebSocket(url, {
        ca:   [root.pem],
        cert: client.pem,
        key:  client_key.pem,
        headers: { "X-Qlik-User": "UserDirectory=corporate; UserId=srv-qlik" },
        rejectUnauthorized: false,
    })

This script does the same thing:

  * Client cert + key come from ``backend/certs/client.pfx``.
  * CA (root) comes from ``backend/certs/root.cer``.
  * Connects directly to the engine on port 4747 (NOT the 443 virtual proxy).
  * Sends the ``X-Qlik-User`` header for Qlik user impersonation.
  * A successful upgrade returns ``HTTP/1.1 101 Switching Protocols``; the script
    then issues a JSON-RPC ``EngineVersion`` call on the Global handle (-1) to
    prove the Engine actually talks back.

Security
--------
The ``client.pfx`` is password-protected. The password is NEVER hardcoded here.
It is read from the ``QLIK_PFX_PASSWORD`` environment variable, or - if that is
unset - requested interactively with a hidden ``getpass`` prompt. The extracted
private key is written to a temporary file only for the lifetime of the
connection and deleted immediately afterwards.

Run
---
    # option A: prompt for the PFX password (hidden input)
    python ws_handshake_test.py

    # option B: pass it via environment variable
    $env:QLIK_PFX_PASSWORD = "..."      # PowerShell
    python ws_handshake_test.py

Optional overrides (environment variables)::

    QLIK_ENGINE_HOST   default: ms16-p-0295.dcsg.com
    QLIK_ENGINE_PORT   default: 4747
    QLIK_ENGINE_USER          default: UserDirectory=corporate; UserId=srv-qlik
    QLIK_APP_ID        default: engineData   (Global-only connection)
    QLIK_ENGINE_PATH   default: /app         (reference JS uses /sense/app)
    QLIK_VERIFY_SSL    default: false        (matches rejectUnauthorized: false)
    QLIK_CLIENT_PFX    default: <this dir>/certs/client.pfx
    QLIK_ROOT_CER      default: <this dir>/certs/root.cer
"""

from __future__ import annotations

import getpass
import json
import os
import socket
import ssl
import tempfile
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Load backend/.env then project-root .env (do not override real env vars).
    _HERE = Path(__file__).resolve().parent
    load_dotenv(_HERE / ".env")
    load_dotenv(_HERE.parent / ".env")
except ImportError:  # pragma: no cover - dotenv optional
    pass

from cryptography import x509
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    pkcs12,
)

try:
    import websocket  # websocket-client (already a project dependency)
except ImportError as exc:  # pragma: no cover
    raise SystemExit("websocket-client is required: pip install websocket-client") from exc


# --- Configuration -----------------------------------------------------------
CERTS_DIR = Path(__file__).resolve().parent / "certs"

QLIK_ENGINE_HOST = os.environ.get("QLIK_ENGINE_HOST", "ms16-p-0295.dcsg.com")
QLIK_ENGINE_PORT = int(os.environ.get("QLIK_ENGINE_PORT", "4747"))
QLIK_ENGINE_USER = os.environ.get("QLIK_ENGINE_USER", "UserDirectory=corporate; UserId=srv-qlik")
QLIK_APP_ID = os.environ.get("QLIK_APP_ID", "engineData")  # engineData => Global-only session
QLIK_ENGINE_PATH = os.environ.get("QLIK_ENGINE_PATH", "/app").rstrip("/")
VERIFY_SSL = os.environ.get("QLIK_VERIFY_SSL", "false").lower() == "true"

CLIENT_PFX = Path(os.environ.get("QLIK_CLIENT_PFX") or (CERTS_DIR / "client.pfx"))
ROOT_CER = Path(os.environ.get("QLIK_ROOT_CER") or (CERTS_DIR / "root.cer"))

TIMEOUT_SECONDS = 20


def _require_file(path: Path, label: str) -> None:
    """Validate that a configured certificate path points to a readable file."""
    if not path.exists():
        raise SystemExit(f"{label} not found: {path}")
    if not path.is_file():
        raise SystemExit(
            f"{label} must be a file, but got: {path} "
            "(check your QLIK_CLIENT_PFX / QLIK_ROOT_CER env settings)"
        )


def rule(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# --- Certificate material -----------------------------------------------------
def _pfx_password() -> bytes:
    """Resolve the PFX password from env var or a hidden interactive prompt."""
    env = os.environ.get("QLIK_PFX_PASSWORD")
    if env is not None:
        return env.encode()
    return getpass.getpass(f"Password for {CLIENT_PFX.name}: ").encode()


def load_client_pem(password: bytes) -> tuple[bytes, bytes]:
    """Load client.pfx and return (cert_chain_pem, private_key_pem)."""
    try:
        data = CLIENT_PFX.read_bytes()
    except OSError as exc:
        raise ValueError(f"unable to read client PFX at {CLIENT_PFX}: {exc}") from exc
    key, cert, extra = pkcs12.load_key_and_certificates(data, password)
    if key is None or cert is None:
        raise ValueError("client.pfx did not contain both a certificate and a private key")

    cert_pem = cert.public_bytes(Encoding.PEM)
    for ca in extra or []:
        cert_pem += ca.public_bytes(Encoding.PEM)

    key_pem = key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    return cert_pem, key_pem


def load_root_pem() -> bytes:
    """Load root.cer (PEM or DER) and return it as PEM bytes."""
    try:
        raw = ROOT_CER.read_bytes()
    except OSError as exc:
        raise ValueError(f"unable to read root certificate at {ROOT_CER}: {exc}") from exc
    try:
        cert = x509.load_pem_x509_certificate(raw)
    except ValueError:
        cert = x509.load_der_x509_certificate(raw)
    return cert.public_bytes(Encoding.PEM)


def _write_temp(data: bytes, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


# --- Engine connection --------------------------------------------------------
def engine_url() -> str:
    return f"wss://{QLIK_ENGINE_HOST}:{QLIK_ENGINE_PORT}{QLIK_ENGINE_PATH}/{QLIK_APP_ID}"


def preflight_tcp() -> bool:
    """Check raw TCP reachability of the engine port and explain a timeout."""
    print(f"\nPreflight   : TCP connect to {QLIK_ENGINE_HOST}:{QLIK_ENGINE_PORT} ...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        addr = socket.gethostbyname(QLIK_ENGINE_HOST)
        print(f"              resolves to {addr}")
        sock.connect((QLIK_ENGINE_HOST, QLIK_ENGINE_PORT))
        print(f"              TCP OPEN -> port {QLIK_ENGINE_PORT} is reachable")
        return True
    except socket.timeout:
        print(f"              TCP TIMEOUT -> port {QLIK_ENGINE_PORT} is FILTERED (firewall silently dropping packets).")
        print("              The engine is not reachable from this host on this port.")
        print("              Run this from a host with direct access to the Qlik engine (e.g. the")
        print("              box that runs loadScriptExport.js), or ask infra to open the port.")
        return False
    except OSError as exc:
        print(f"              TCP ERROR -> {exc}")
        return False
    finally:
        sock.close()


def test_engine_connection() -> None:
    rule("Qlik ENGINE WebSocket over mutual-TLS  (expected: 101 + EngineVersion)")

    url = engine_url()
    print(f"Target engine : {url}")
    print(f"X-Qlik-User   : {QLIK_ENGINE_USER}")
    print(f"Client cert   : {CLIENT_PFX}")
    print(f"Root CA       : {ROOT_CER}")
    print(f"Verify TLS    : {VERIFY_SSL}")

    if not preflight_tcp():
        return

    try:
        cert_pem, key_pem = load_client_pem(_pfx_password())
        root_pem = load_root_pem()
    except ValueError as exc:
        print(f"\n  RESULT -> CERT ERROR: {exc}")
        print("  ==> Check the PFX password (QLIK_PFX_PASSWORD) and cert files.")
        return

    cert_path = _write_temp(cert_pem, "_client.pem")
    key_path = _write_temp(key_pem, "_client_key.pem")
    ca_path = _write_temp(root_pem, "_root.pem")

    # Mirror the JS ws options: ca/cert/key + rejectUnauthorized:false
    sslopt = {
        "certfile": cert_path,
        "keyfile": key_path,
        "ca_certs": ca_path,
        "cert_reqs": ssl.CERT_REQUIRED if VERIFY_SSL else ssl.CERT_NONE,
        "check_hostname": VERIFY_SSL,
    }
    header = [f"X-Qlik-User: {QLIK_ENGINE_USER}"]

    ws = None
    try:
        ws = websocket.create_connection(
            url,
            header=header,
            sslopt=sslopt,
            timeout=TIMEOUT_SECONDS,
        )
        print("\n  RESULT -> HTTP/1.1 101 Switching Protocols")
        print("  ==> SUCCESS: mutual-TLS WebSocket upgrade accepted")

        version = engine_version(ws)
        if version is not None:
            print(f"  ==> Engine responded to JSON-RPC. EngineVersion = {version}")
    except websocket.WebSocketBadStatusException as exc:
        print(f"\n  RESULT -> {exc}")
        print("  ==> BLOCKED: engine rejected the upgrade (expected 101 Switching Protocols)")
    except Exception as exc:  # noqa: BLE001 - diagnostic surface
        print(f"\n  RESULT -> CONNECTION ERROR: {type(exc).__name__}: {exc}")
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:  # pragma: no cover
                pass
        for p in (cert_path, key_path, ca_path):
            try:
                os.remove(p)
            except OSError:
                pass


def engine_version(ws: "websocket.WebSocket") -> str | None:
    """Issue a Global-handle EngineVersion call and return qComponentVersion."""
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "handle": -1,
        "method": "EngineVersion",
        "params": [],
    }
    ws.send(json.dumps(request))
    # The Engine may push OnConnected / OnAuthenticationInformation first.
    for _ in range(10):
        raw = ws.recv()
        if not raw:
            return None
        message = json.loads(raw)
        if message.get("id") != 1:
            continue
        if "error" in message:
            print(f"  ==> Engine JSON-RPC error: {message['error']}")
            return None
        result = message.get("result", {})
        qv = result.get("qVersion") or {}
        return qv.get("qComponentVersion") or json.dumps(result)
    return None


def main() -> None:
    print("Qlik Engine mutual-TLS handshake diagnostic")
    _require_file(CLIENT_PFX, "client cert")
    _require_file(ROOT_CER, "root cert")
    test_engine_connection()


if __name__ == "__main__":
    main()
