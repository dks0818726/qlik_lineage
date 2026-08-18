from fastapi import APIRouter
from pydantic import BaseModel

from app.lineage.builder import LineageBuilder
from app.models.entities import ParsedDependency

router = APIRouter(prefix="/lineage", tags=["lineage"])


class BuildLineageRequest(BaseModel):
    dependencies: list[dict[str, str | None]]


@router.post("/build")
def build_lineage(payload: BuildLineageRequest) -> dict[str, object]:
    deps = [ParsedDependency(app_id=item["app_id"], **{k: v for k, v in item.items() if k != "app_id"}) for item in payload.dependencies]
    edges = LineageBuilder().build_edges(deps)
    return {"count": len(edges), "edges": [edge.__dict__ for edge in edges]}
