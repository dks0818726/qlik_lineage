from fastapi import APIRouter
from pydantic import BaseModel

from app.parser.qlik_parser import QlikScriptParser

router = APIRouter(prefix="/parser", tags=["parser"])


class ParseRequest(BaseModel):
    app_id: str
    script: str


@router.post("/extract")
def extract_dependencies(payload: ParseRequest) -> dict[str, object]:
    parser = QlikScriptParser()
    records = parser.parse(app_id=payload.app_id, script=payload.script)
    return {"count": len(records), "dependencies": [record.__dict__ for record in records]}
