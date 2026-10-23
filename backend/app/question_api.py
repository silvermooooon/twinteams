"""Authenticated ordinary answers, separate from approvals and secret entry."""
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from . import questions
from .api import OwnerIdentity
from .service import service

router = APIRouter(prefix="/api")


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str


@router.post("/questions/{question_id}/answer")
async def answer_question(question_id: str, data: Answer, owner: OwnerIdentity):
    return await service.db_call(questions.answer, service, owner, question_id, data.answer)


@router.post("/questions/{question_id}/dismiss")
async def dismiss_question(question_id: str, owner: OwnerIdentity):
    return await service.db_call(questions.dismiss, service, owner, question_id)
