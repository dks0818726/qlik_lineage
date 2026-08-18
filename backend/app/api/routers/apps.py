from fastapi import APIRouter, HTTPException

from app.dependencies import get_neo4j, get_repository

router = APIRouter(prefix="/apps", tags=["apps"])


@router.get("")
def list_apps(limit: int = 200) -> dict[str, object]:
    repo = get_repository()
    rows = repo.list_apps(limit=limit)
    return {"count": len(rows), "apps": rows}


@router.get("/{app_id}")
def app_details(app_id: str) -> dict[str, object]:
    repo = get_repository()
    app = repo.get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"App not found: {app_id}")
    script = repo.get_script(app_id)
    neo = get_neo4j()
    return {
        "app": app,
        "script_excerpt": (script or "")[:2000],
        "upstream": neo.upstream("App", app_id, depth=3),
        "downstream": neo.downstream("App", app_id, depth=3),
    }
