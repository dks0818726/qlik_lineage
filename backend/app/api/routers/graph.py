from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.dependencies import get_neo4j

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/upstream")
def upstream(node_type: str, node_id: str, depth: int = Query(default=5, ge=1, le=20)) -> dict[str, object]:
    chains = get_neo4j().upstream(node_type, node_id, depth)
    return {"node": {"type": node_type, "id": node_id}, "chains": chains}


@router.get("/downstream")
def downstream(node_type: str, node_id: str, depth: int = Query(default=5, ge=1, le=20)) -> dict[str, object]:
    chains = get_neo4j().downstream(node_type, node_id, depth)
    return {"node": {"type": node_type, "id": node_id}, "chains": chains}


@router.get("/impact")
def impact(node_type: str, node_id: str, depth: int = Query(default=5, ge=1, le=20)) -> dict[str, object]:
    nodes = get_neo4j().impact(node_type, node_id, depth)
    return {"node": {"type": node_type, "id": node_id}, "impacted": nodes}


@router.get("/neighborhood")
def neighborhood(node_type: str, node_id: str, depth: int = Query(default=2, ge=1, le=5)) -> dict[str, object]:
    return get_neo4j().neighborhood(node_type, node_id, depth)


class CypherRequest(BaseModel):
    statement: str


@router.post("/cypher")
def cypher(payload: CypherRequest) -> dict[str, object]:
    try:
        rows = get_neo4j().run_cypher(payload.statement)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return {"rows": rows, "count": len(rows)}
