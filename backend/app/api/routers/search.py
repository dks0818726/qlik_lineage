from fastapi import APIRouter, Query

from app.dependencies import get_repository

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
def search(
    q: str = Query(..., min_length=1),
    type: str | None = Query(default=None, description="App|QVD|Table|Task"),
    limit: int = Query(default=25, ge=1, le=200),
) -> dict[str, object]:
    repo = get_repository()
    results = repo.search_nodes(q, type_filter=type, limit=limit)
    return {"count": len(results), "results": results}
