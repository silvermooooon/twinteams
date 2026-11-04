"""Authenticated owner answers and explicitly selected question options."""
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, StrictBool

from . import questions
from .api import OwnerIdentity
from .service import service

router = APIRouter(prefix="/api")


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str
    selected_option: StrictBool = False


@router.post("/questions/{question_id}/answer")
async def answer_question(question_id: str, data: Answer, owner: OwnerIdentity):
    return await service.db_call(questions.answer, service, owner, question_id, data.answer, data.selected_option)


@router.post("/questions/{question_id}/dismiss")
async def dismiss_question(question_id: str, owner: OwnerIdentity):
    return await service.db_call(questions.dismiss, service, owner, question_id)
