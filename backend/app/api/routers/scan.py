from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.dependencies import get_orchestrator

router = APIRouter(prefix="/scan", tags=["scan"])


class ScanRequest(BaseModel):
    mode: str = "full"  # "full" | "delta"


@router.post("/run")
def run_scan(payload: ScanRequest, background: BackgroundTasks) -> dict[str, object]:
    if payload.mode not in {"full", "delta"}:
        raise HTTPException(400, "mode must be 'full' or 'delta'")
    orchestrator = get_orchestrator()
    background.add_task(orchestrator.run, payload.mode)
    return {"queued": True, "mode": payload.mode}


@router.post("/sync")
def run_scan_sync(payload: ScanRequest) -> dict[str, object]:
    if payload.mode not in {"full", "delta"}:
        raise HTTPException(400, "mode must be 'full' or 'delta'")
    return get_orchestrator().run(payload.mode)


class ReparseRequest(BaseModel):
    app_ids: list[str] | None = None


@router.post("/reparse")
def reparse(payload: ReparseRequest | None = None) -> dict[str, object]:
    """Rebuild lineage from stored scripts, without contacting Qlik.

    Use after the parser changes: a normal scan skips apps whose script hash is
    unchanged, so parser improvements would otherwise not be applied.
    """
    body = payload or ReparseRequest()
    return get_orchestrator().reparse_stored_scripts(body.app_ids)


@router.post("/app-status")
def refresh_app_status() -> dict[str, object]:
    """Re-classify every app (live / dev copy / stale / unscheduled) from QRS.

    Only app and reload-task metadata is fetched; no scripts are loaded or parsed,
    so this takes seconds. Run it after changing APP_STALE_DAYS.
    """
    try:
        return get_orchestrator().refresh_app_statuses()
    except Exception as exc:  # noqa: BLE001 - surface QRS failures to the caller
        raise HTTPException(502, f"Could not refresh app status from QRS: {exc}") from exc
