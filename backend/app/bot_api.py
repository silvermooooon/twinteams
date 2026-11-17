"""Authenticated owner controls for personal BOTs and internal coordination."""
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import Field

from . import personal_bots
from .api import Input, OwnerIdentity
from .service import service

router = APIRouter(prefix="/api")
AgentIds = list[Annotated[str, Field(min_length=1, max_length=80)]]


class BotProfile(Input):
    name: str = Field(min_length=1, max_length=128)
    assignment: str = Field(min_length=1)


class BotDispatch(Input):
    bot_ids: AgentIds = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1)
    client_request_key: str = Field(min_length=1, max_length=160)


class AgentMessage(Input):
    sender_actor_id: str = Field(min_length=1, max_length=80)
    recipient_actor_id: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1)
    client_request_key: str = Field(min_length=1, max_length=160)


class BotRoom(Input):
    title: str = Field(min_length=1, max_length=256)
    bot_ids: AgentIds = Field(min_length=1, max_length=100)


@router.get("/bots")
def list_bots(owner: OwnerIdentity):
    return personal_bots.list_bots(service, owner)


@router.post("/bots")
def create_bot(data: BotProfile, owner: OwnerIdentity):
    return personal_bots.create_bot(service, owner, data.name, data.assignment)


@router.patch("/bots/{bot_id}")
def edit_bot(bot_id: str, data: BotProfile, owner: OwnerIdentity):
    return personal_bots.edit_bot(service, owner, bot_id, data.name, data.assignment)


@router.get("/bots/{bot_id}/history")
def history(bot_id: str, owner: OwnerIdentity, limit: int = Query(30, ge=1, le=200),
            before_seq: int | None = Query(None, ge=1)):
    return personal_bots.bot_history(service, owner, bot_id, limit, before_seq)


@router.post("/bots/dispatch")
def dispatch_tasks(data: BotDispatch, owner: OwnerIdentity):
    return personal_bots.dispatch_tasks(service, owner, data.bot_ids, data.body, data.client_request_key)


@router.post("/bots/messages")
def message_agent(data: AgentMessage, owner: OwnerIdentity):
    return personal_bots.message_agent(service, owner, data.sender_actor_id,
        data.recipient_actor_id, data.body, data.client_request_key)


@router.post("/bot-rooms")
def create_room(data: BotRoom, owner: OwnerIdentity):
    return personal_bots.create_room(service, owner, data.title, data.bot_ids)
