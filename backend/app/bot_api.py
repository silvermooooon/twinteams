"""Authenticated owner controls for personal BOTs and internal coordination."""
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import Field

from . import bot_sharing, bot_skills, personal_bots
from .api import Input, OwnerIdentity
from .personal_api import SkillPatch, _read_skill_folder
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


class ShareAction(Input):
    client_request_key: str = Field(min_length=1, max_length=160)


class ShareLookup(Input):
    token: str = Field(min_length=1, max_length=64)


class SkillEdit(Input):
    content: str = Field(min_length=1, max_length=20 * 1024 * 1024)


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


@router.get("/bots/{bot_id}/shares")
def list_shares(bot_id: str, owner: OwnerIdentity):
    return bot_sharing.list_shares(service, owner, bot_id)


@router.post("/bots/{bot_id}/shares")
def freeze_share(bot_id: str, data: ShareAction, owner: OwnerIdentity):
    return bot_sharing.freeze_share(service, owner, bot_id, data.client_request_key)


@router.post("/bots/{bot_id}/shares/{snapshot_id}/tokens")
def issue_token(bot_id: str, snapshot_id: str, data: ShareAction, owner: OwnerIdentity):
    return bot_sharing.issue_token(service, owner, bot_id, snapshot_id, data.client_request_key)


@router.delete("/bots/{bot_id}/shares/tokens/{token_id}")
def revoke_token(bot_id: str, token_id: str, owner: OwnerIdentity):
    return bot_sharing.revoke_token(service, owner, bot_id, token_id)


@router.post("/bot-shares/preview")
def preview_share(data: ShareLookup, owner: OwnerIdentity):
    return bot_sharing.preview_share(service, owner, data.token)


@router.post("/bot-shares/install")
def install_share(data: ShareLookup, owner: OwnerIdentity):
    return bot_sharing.install_share(service, owner, data.token)


@router.get("/bots/{bot_id}/skills")
async def bot_skill_catalog(bot_id: str, owner: OwnerIdentity):
    return await bot_skills.discover_skills(owner, bot_id)


@router.post("/bots/{bot_id}/skills/upload")
async def upload_bot_skill(bot_id: str, request: Request, owner: OwnerIdentity):
    name, files = await _read_skill_folder(request)
    return await bot_skills.upload_skill(owner, bot_id, name, files)


@router.get("/bots/{bot_id}/skills/{name}")
async def bot_skill_detail(bot_id: str, name: str, owner: OwnerIdentity):
    return await bot_skills.skill_detail(owner, bot_id, name)


@router.patch("/bots/{bot_id}/skills/{name}")
async def toggle_bot_skill(bot_id: str, name: str, data: SkillPatch, owner: OwnerIdentity):
    return await bot_skills.patch_skill(owner, bot_id, name, data.enabled)


@router.put("/bots/{bot_id}/skills/{name}")
async def edit_bot_skill(bot_id: str, name: str, data: SkillEdit, owner: OwnerIdentity):
    return await bot_skills.edit_skill(owner, bot_id, name, data.content)


@router.delete("/bots/{bot_id}/skills/{name}")
async def delete_bot_skill(bot_id: str, name: str, owner: OwnerIdentity):
    return await bot_skills.delete_skill(owner, bot_id, name)
