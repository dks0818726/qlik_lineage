from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass
class QrsClient:
    """Qlik Repository Service (QRS) read-only client.

    Access is exclusively through the ``/custom`` virtual proxy using HTTP
    **header authentication** — no NTLM, no direct QRS port 4242, and no client
    certificates. Every request carries the ``X-Qlik-Xrfkey`` and
    ``X-Qlik-User`` headers plus the matching ``xrfkey`` query parameter.

    Example endpoint::

        https://10.221.11.6/custom/qrs/app/full?xrfkey=1234567890abcdef

    All calls are GETs; no writes are issued.
    """

    base_url: str  = "/custom"                     # MUST include the /custom virtual proxy prefix
    qlik_user: str = "CORPORATE\\srv-qlik"                 # X-Qlik-User header, e.g. CORPORATE\\srv-qlik
    xrf_key: str = "1234567890abcdef"   # X-Qlik-Xrfkey header + xrfkey query param
    verify_ssl: bool | str = True
    timeout_seconds: int = 30
    _session: requests.Session = field(default=None, init=False, repr=False)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._session = requests.Session()
        self._session.verify = self.verify_ssl

    # -- fetchers -------------------------------------------------------------
    def fetch_apps(self, filter_expr: str | None = None) -> list[dict[str, Any]]:
        return self._get("/qrs/app/full", filter_expr=filter_expr)

    def fetch_streams(self) -> list[dict[str, Any]]:
        return self._get("/qrs/stream/full")

    def fetch_reload_tasks(self) -> list[dict[str, Any]]:
        return self._get("/qrs/reloadtask/full")

    def fetch_schedules(self) -> list[dict[str, Any]]:
        return self._get("/qrs/schemaevent/full")

    def fetch_data_connections(self) -> list[dict[str, Any]]:
        return self._get("/qrs/dataconnection/full")

    def fetch_owners(self) -> list[dict[str, Any]]:
        return self._get("/qrs/user/full")

    def fetch_task_dependencies(self) -> list[dict[str, Any]]:
        return self._get("/qrs/compositeevent/full")

    # -- HTTP ----------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {
            "X-Qlik-Xrfkey": self.xrf_key,
            "Content-Type": "application/json",
        }
        if self.qlik_user:
            headers["X-Qlik-User"] = self.qlik_user
        return headers

    def _get(self, path: str, filter_expr: str | None = None) -> list[dict[str, Any]]:
        url = f"{self.base_url.rstrip('/')}{path}"
        params: dict[str, Any] = {"xrfkey": self.xrf_key}
        if filter_expr:
            params["filter"] = filter_expr
        response = self._session.get(
            url, params=params, headers=self._headers(), timeout=self.timeout_seconds
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return payload
        raise ValueError(f"Unexpected QRS payload type: {type(payload)}")
