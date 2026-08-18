from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import requests

from app.connectors.engine_session import EngineSession

logger = logging.getLogger(__name__)


@dataclass
class EngineClient:
    """Qlik Engine read-only facade.

    Two transports:

    * WebSocket JSON-RPC (Qlik Sense native) — a **direct mutual-TLS** connection
      to the engine host on port 4747, authenticated with a client certificate
      (``client.pfx`` + ``root.cer``) and the ``X-Qlik-User`` header. Enabled
      whenever ``engine_host`` is set. Uses the enigma.js-style lifecycle:
      ``open session → OpenDoc(qNoData=true) → GetScript → CloseDoc → close``.
    * HTTP (mock/dev proxy) — used only for local development and unit tests
      when no ``engine_host`` is configured.
    """

    base_url: str = ""                     # HTTP mock/dev base (used only when no engine_host)
    qlik_user: str = ""                    # X-Qlik-User header, e.g. UserDirectory=corporate; UserId=srv-qlik
    engine_host: str = ""                  # Qlik Engine host, e.g. ms16-p-0295.dcsg.com
    engine_port: int = 4747
    engine_path: str = "/app"              # yields wss://<host>:4747/app/<APP_ID>
    client_pfx: str = ""                   # path to client.pfx
    pfx_password: str = ""                 # password for client.pfx
    root_cer: str = ""                     # path to root CA (root.cer)
    verify_ssl: bool = False
    timeout_seconds: int = 30
    export_scripts: bool = False
    export_scripts_dir: str = ""
    _session: requests.Session = field(default=None, init=False, repr=False)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._session = requests.Session()
        self._session.verify = self.verify_ssl

    # -- capability check -----------------------------------------------------
    @property
    def has_websocket(self) -> bool:
        return bool(self.engine_host)

    def _engine_url_template(self) -> str:
        """wss://<host>:<port><path> — the app_id is appended by EngineSession."""
        return f"wss://{self.engine_host}:{self.engine_port}{self.engine_path.rstrip('/')}"

    def open_session(self, app_id: str) -> EngineSession:
        """Create an open Engine session for a single app (matches enigma.js ``create()``)."""
        if not self.has_websocket:
            raise RuntimeError("Engine host not configured (set QLIK_ENGINE_HOST)")
        session = EngineSession(
            engine_url_template=self._engine_url_template(),
            qlik_user=self.qlik_user,
            client_pfx=self.client_pfx,
            pfx_password=self.pfx_password,
            root_cer=self.root_cer,
            verify_ssl=self.verify_ssl,
            timeout_seconds=self.timeout_seconds,
        )
        return session.open(app_id)

    # -- public API (per-app one-shot helpers) --------------------------------
    def get_load_script(self, app_id: str) -> str:
        if self.has_websocket:
            with self.open_session(app_id) as session:
                doc_handle = session.open_doc(app_id, no_data=True)
                try:
                    script = session.get_script(doc_handle)
                finally:
                    session.close_doc(doc_handle)
            if self.export_scripts:
                self._dump_script(app_id, script)
            return script
        payload = self._http_get(f"/engine/{app_id}/script")
        script = payload.get("script")
        if not isinstance(script, str):
            raise ValueError("Engine script payload missing string `script` key")
        if self.export_scripts:
            self._dump_script(app_id, script)
        return script

    def get_app_metadata(self, app_id: str) -> dict[str, Any]:
        if self.has_websocket:
            with self.open_session(app_id) as session:
                doc_handle = session.open_doc(app_id, no_data=True)
                try:
                    return session.get_app_layout(doc_handle)
                finally:
                    session.close_doc(doc_handle)
        return self._http_get(f"/engine/{app_id}/metadata")

    def get_tables_and_keys(self, app_id: str) -> list[dict[str, Any]]:
        if self.has_websocket:
            with self.open_session(app_id) as session:
                doc_handle = session.open_doc(app_id, no_data=True)
                try:
                    return session.get_tables_and_keys(doc_handle)
                finally:
                    session.close_doc(doc_handle)
        payload = self._http_get(f"/engine/{app_id}/tables")
        tables = payload.get("tables", [])
        if not isinstance(tables, list):
            raise ValueError("Engine table payload invalid")
        return tables

    # -- helpers --------------------------------------------------------------
    def _dump_script(self, app_id: str, script: str) -> None:
        target_dir = self.export_scripts_dir or os.path.join(os.getcwd(), "exported_scripts")
        try:
            os.makedirs(target_dir, exist_ok=True)
            path = os.path.join(target_dir, f"{app_id}.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(script)
            logger.info("Exported load script for app %s to %s", app_id, path)
        except OSError as exc:
            logger.warning("Failed to export script for %s: %s", app_id, exc)

    def _http_get(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}{path}"
        headers: dict[str, str] = {}
        if self.qlik_user:
            headers["X-Qlik-User"] = self.qlik_user
        response = self._session.get(url, headers=headers, timeout=self.timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected Engine payload type: {type(payload)}")
        return payload
