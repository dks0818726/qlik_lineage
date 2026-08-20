from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from app.dependencies import get_doc_generator, get_neo4j, get_repository
from app.docs.generator import AmbiguousApp, AppNotFound

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


# -- generated documentation -------------------------------------------------
# Generation and retrieval are deliberately separate verbs so that a page
# refresh or a second download never silently triggers another paid LLM call.

@router.post("/{app_ref}/documentation")
def generate_documentation(app_ref: str) -> dict[str, object]:
    """Generate documentation for an app. `app_ref` may be an app id or a name."""
    try:
        result = get_doc_generator().generate(app_ref)
    except AmbiguousApp as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc),
                    "candidates": [
                        {"app_id": c["app_id"], "name": c["name"]}
                        for c in exc.candidates[:25]
                    ]},
        ) from exc
    except AppNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "written",
        "app_id": result.app_id,
        "app": result.app_name,
        "filename": result.filename,
        "path": result.path,
        "bytes": result.bytes,
        "sections": result.sections,
        "evidence_level": result.evidence_level,
        "evidence": result.evidence_label,
        "model": result.model,
    }


@router.get("/{app_id}/documentation")
def fetch_documentation(app_id: str, format: str = "json"):
    """Return stored documentation. `?format=raw` serves it as text/markdown."""
    doc = get_repository().get_documentation(app_id)
    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"No documentation generated yet for app {app_id}",
        )
    if format == "raw":
        return PlainTextResponse(doc["markdown"], media_type="text/markdown")
    return {
        "app_id": doc["app_id"],
        "app": doc["app_name"],
        "filename": doc["filename"],
        "markdown": doc["markdown"],
        "sections": doc["sections"],
        "evidence_level": doc["evidence_level"],
        "model": doc["model"],
        "generated_at": doc["generated_at"],
        # True when the load script changed after this document was written.
        "stale": doc["stale"],
    }
