"""Qlik Engine JSON-RPC session that mirrors the enigma.js workflow.

Flow (mirrors the reference ``loadScriptExport.js``)::

    session = EngineSession(...).open(app_id)   # WebSocket + Global handle -1
    doc = session.open_doc(app_id, no_data=True)
    script = session.get_script(doc)
    session.close_doc(doc)
    session.close()

Unlike QRS (which goes through the ``/custom`` virtual proxy), the Engine
connection is a **direct mutual-TLS WebSocket** to the engine host on port 4747,
authenticated with a client certificate (``client.pfx`` + ``root.cer``) and the
``X-Qlik-User`` impersonation header — exactly like ``ws_handshake_test.py``.

The session is a one-app affair (Qlik Engine binds the WebSocket URL to the app)
and MUST be closed after use – matching the JS reference exactly.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    pkcs12,
)

try:
    import websocket  # websocket-client
except ImportError:  # pragma: no cover - optional in local/dev environments
    websocket = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _load_client_pem(pfx_path: str, password: str) -> tuple[bytes, bytes]:
    """Load client.pfx and return (cert_chain_pem, private_key_pem)."""
    key, cert, extra = pkcs12.load_key_and_certificates(
        Path(pfx_path).read_bytes(), password.encode() if password else None
    )
    if key is None or cert is None:
        raise ValueError("client.pfx did not contain both a certificate and a private key")
    cert_pem = cert.public_bytes(Encoding.PEM)
    for ca in extra or []:
        cert_pem += ca.public_bytes(Encoding.PEM)
    key_pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    return cert_pem, key_pem


def _load_root_pem(cer_path: str) -> bytes:
    """Load root.cer (PEM or DER) and return it as PEM bytes."""
    raw = Path(cer_path).read_bytes()
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


@dataclass
class EngineSession:
    """Single-app Qlik Engine JSON-RPC session (open → ops → close).

    Connects **directly** to the Qlik Engine (port 4747) over mutual TLS using a
    client certificate, plus the ``X-Qlik-User`` header for impersonation.
    """

    engine_url_template: str  # e.g. wss://ms16-p-0295.dcsg.com:4747/app (app_id appended)
    qlik_user: str = ""       # X-Qlik-User header, e.g. UserDirectory=corporate; UserId=srv-qlik
    client_pfx: str = ""      # path to client.pfx
    pfx_password: str = ""    # password for client.pfx
    root_cer: str = ""        # path to root CA (root.cer)
    verify_ssl: bool = False
    timeout_seconds: int = 30
    _ws: Any = field(default=None, init=False, repr=False)
    _request_id: int = field(default=0, init=False, repr=False)
    _temp_files: list[str] = field(default_factory=list, init=False, repr=False)

    # -- lifecycle ------------------------------------------------------------
    def open(self, app_id: str) -> "EngineSession":
        if websocket is None:
            raise RuntimeError("websocket-client not installed; cannot open Engine session")
        url = self._build_url(app_id)

        cert_pem, key_pem = _load_client_pem(self.client_pfx, self.pfx_password)
        cert_path = _write_temp(cert_pem, "_client.pem")
        key_path = _write_temp(key_pem, "_client_key.pem")
        ca_path = _write_temp(_load_root_pem(self.root_cer), "_root.pem")
        self._temp_files = [cert_path, key_path, ca_path]

        sslopt = {
            "certfile": cert_path,
            "keyfile": key_path,
            "ca_certs": ca_path,
            "cert_reqs": ssl.CERT_REQUIRED if self.verify_ssl else ssl.CERT_NONE,
            "check_hostname": self.verify_ssl,
        }
        header = [f"X-Qlik-User: {self.qlik_user}"] if self.qlik_user else []
        logger.debug("Opening Engine WebSocket (mutual-TLS) to %s", url)
        try:
            self._ws = websocket.create_connection(
                url,
                header=header,
                sslopt=sslopt,
                timeout=self.timeout_seconds,
            )
        except Exception:
            # Never leave the decrypted key/cert PEMs on disk if the upgrade fails.
            self.close()
            raise
        return self

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # pragma: no cover - defensive
                logger.debug("Error closing Engine WebSocket", exc_info=True)
            finally:
                self._ws = None
        for path in self._temp_files:
            try:
                os.remove(path)
            except OSError:  # pragma: no cover - defensive
                pass
        self._temp_files = []

    def __enter__(self) -> "EngineSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- Engine JSON-RPC calls -----------------------------------------------
    def open_doc(self, app_id: str, no_data: bool = True) -> int:
        """OpenDoc on the Global handle (-1); returns the resulting document handle."""
        # Named params match enigma.js: { qDocName, qNoData }
        params = {"qDocName": app_id, "qNoData": no_data}
        result = self._call(handle=-1, method="OpenDoc", params=params)
        qreturn = result.get("qReturn") or {}
        handle = qreturn.get("qHandle")
        if not isinstance(handle, int):
            raise RuntimeError(f"OpenDoc did not return qHandle: {result!r}")
        return handle

    def get_script(self, doc_handle: int) -> str:
        result = self._call(handle=doc_handle, method="GetScript", params={})
        script = result.get("qScript")
        if not isinstance(script, str):
            raise RuntimeError(f"GetScript returned no qScript: {result!r}")
        return script

    def get_app_layout(self, doc_handle: int) -> dict[str, Any]:
        result = self._call(handle=doc_handle, method="GetAppLayout", params={})
        layout = result.get("qLayout")
        if isinstance(layout, dict):
            return layout
        return result

    def get_tables_and_keys(self, doc_handle: int) -> list[dict[str, Any]]:
        params = {
            "qWindowSize": {"qcx": 0, "qcy": 0},
            "qNullSize": {"qcx": 0, "qcy": 0},
            "qCellHeight": 0,
            "qSyntheticMode": True,
            "qIncludeSysVars": False,
        }
        result = self._call(handle=doc_handle, method="GetTablesAndKeys", params=params)
        tables = result.get("qtr") or result.get("qTables") or []
        return tables if isinstance(tables, list) else []

    def close_doc(self, doc_handle: int) -> None:
        try:
            self._call(handle=doc_handle, method="CloseDoc", params={})
        except Exception:  # pragma: no cover - Qlik occasionally closes doc automatically
            logger.debug("CloseDoc failed (harmless if session is closing)", exc_info=True)

    # -- transport helpers ---------------------------------------------------
    def _build_url(self, app_id: str) -> str:
        base = self.engine_url_template.rstrip("/")
        if "{app_id}" in base:
            return base.replace("{app_id}", app_id)
        if base.endswith(app_id):
            return base
        return f"{base}/{app_id}"

    def _call(self, handle: int, method: str, params: Any) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("EngineSession is not open")
        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "handle": handle,
            "method": method,
            "params": params,
        }
        self._ws.send(json.dumps(request))
        # Engine may push unsolicited "OnConnected"/"OnAuthenticationInformation"
        # notifications before the response. Loop until we match our request id.
        while True:
            raw = self._ws.recv()
            if not raw:
                raise RuntimeError(f"Engine WS closed while awaiting {method}")
            response = json.loads(raw)
            if response.get("id") != request["id"]:
                # notification / event – ignore
                continue
            if "error" in response:
                raise RuntimeError(f"Engine JSON-RPC error on {method}: {response['error']}")
            result = response.get("result", {})
            if not isinstance(result, dict):
                raise RuntimeError(f"Unexpected Engine result for {method}: {result!r}")
            return result
