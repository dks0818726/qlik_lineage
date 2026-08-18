from fastapi import APIRouter
from pydantic import BaseModel

from app.dependencies import get_agent

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentQuestion(BaseModel):
    question: str


@router.post("/ask")
def ask_agent(payload: AgentQuestion) -> dict[str, object]:
    return get_agent().answer(payload.question)
