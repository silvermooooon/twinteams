"""Authenticated JSON API. Local identities are explicitly development-only."""
from datetime import datetime
from typing import Annotated, Literal
import jwt
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request, Response, WebSocket
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from .config import settings
from .service import service

router = APIRouter(prefix="/api")


def identity(authorization: Annotated[str | None, Header()] = None, x_owner_id: Annotated[str | None, Header()] = None, tt_dev_session: Annotated[str | None, Cookie()] = None) -> str:
    if settings.local_identity_enabled:
        if x_owner_id:
            raise HTTPException(401, "Use local username login; owner headers are not authentication")
        return service.authenticate_local(tt_dev_session)
    if x_owner_id:
        raise HTTPException(401, "Development identity headers are disabled outside local development")
    if not settings.production_identity_configured:
        raise HTTPException(503, "Verified identity is not configured; production access is disabled")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "A verified Bearer identity is required")
    try:
        claims = jwt.decode(authorization[7:], settings.jwt_key.replace("\\n", "\n"), algorithms=[settings.jwt_algorithm], audience=settings.jwt_audience, issuer=settings.jwt_issuer, options={"require": ["exp", "iat", "sub", "iss", "aud", "tenant_id"]})
        if claims["tenant_id"] != settings.tenant_id:
            raise jwt.InvalidTokenError("Tenant mismatch")
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired identity token") from None
    return service.owner_for_subject(claims["sub"])


OwnerIdentity = Annotated[str, Depends(identity)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateChat(Input):
    owner_ids: list[str] = Field(min_length=1, max_length=100)
    title: str | None = Field(None, min_length=1, max_length=256)
    history_policy: Literal["all", "fixed_7d", "no_prior"] = "all"


class ChatTitleInput(Input):
    title: str


class AddMembers(Input):
    owner_ids: list[str] = Field(min_length=1, max_length=100)
    history_policy: Literal["all", "fixed_7d", "no_prior"]


class SendMessage(Input):
    body: str = ""
    client_request_key: str = Field(min_length=1, max_length=160)
    reply_to_message_id: str | None = None
    reply_for_me: bool = False
    attachment_ids: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=4)


class MessageReactionInput(Input):
    emoji: str = Field(min_length=1, max_length=32)
    active: bool = Field(strict=True)
    client_request_key: str = Field(min_length=1, max_length=160)


class RequestReply(Input):
    message_id: str
    target_twin_id: str
    client_request_key: str = Field(min_length=1, max_length=160)


class Retry(Input):
    client_request_key: str = Field(min_length=1, max_length=160)


class ContinueAutomaticConversation(Input):
    client_request_key: str = Field(min_length=1, max_length=160)
    additional_replies: int = Field(ge=1, strict=True)
    expected_allowance: int = Field(ge=0, strict=True)


class PresenceInput(Input):
    device_id: str = Field(min_length=1, max_length=128)
    focused_chat_id: str | None = None
    visible: bool


class ReadMessages(Input):
    message_ids: list[str] = Field(max_length=1000)


class ChatSettingsInput(Input):
    reply_for_me_enabled: bool | None = None
    prompt_override: str | None = Field(None, max_length=30000)
    sidebar_open: bool | None = None
    sidebar_tab: Literal["activity", "workspace", "tracking"] | None = None


class OwnerSettingsInput(Input):
    default_reply_prompt: str = Field(max_length=30000)


class Decision(Input):
    decision: Literal["approved", "rejected"]


class TrackingCreate(Input):
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(max_length=30000)
    source_message_ids: list[str] = Field(default_factory=list, max_length=500)


class TrackingPatch(Input):
    expected_revision: int | None = Field(None, ge=1)
    title: str | None = Field(None, min_length=1, max_length=256)
    description: str | None = Field(None, max_length=30000)
    status: str | None = Field(None, min_length=1, max_length=32)


class TrackingUpdateInput(Input):
    body: str = Field(min_length=1, max_length=30000)
    source_message_ids: list[str] = Field(default_factory=list, max_length=500)


class ReminderCreate(Input):
    title: str = Field(min_length=1, max_length=256)
    detail: str = Field(default="", max_length=30000)
    due_at: datetime | None = None
    chat_id: str | None = None


class ReminderPatch(Input):
    done: bool


class LoginInput(Input):
    username: str = Field(min_length=1, max_length=64)


@router.post("/auth/login")
def login(data: LoginInput, response: Response):
    token, account = service.login_local(data.username)
    response.set_cookie("tt_dev_session", token, max_age=43200, httponly=True, secure=False, samesite="strict", path="/")
    return account


@router.get("/auth/me")
def me(owner: OwnerIdentity):
    return service.account(owner)


@router.post("/auth/logout")
def logout(response: Response, tt_dev_session: Annotated[str | None, Cookie()] = None, device_id: str | None = Query(None, max_length=128)):
    if not settings.local_identity_enabled:
        raise HTTPException(404, "Development logout is disabled")
    service.logout_local(tt_dev_session, device_id)
    response.delete_cookie("tt_dev_session", path="/", httponly=True, samesite="strict")
    return {"ok": True}


@router.get("/config")
def config():
    runtime = service.runtime
    provider = getattr(runtime, "configuration_status", {"configured": False})
    provider = {key: provider.get(key) for key in ("configured", "provider", "model", "database")}
    if runtime and getattr(runtime, "startup_error", None):
        provider["error"] = "Runtime storage could not initialize; inspect the server diagnostic"
    return {"development_mode": settings.local_identity_enabled, "identity_mode": "local_development_login" if settings.local_identity_enabled else "verified_jwt", "identity_configured": settings.local_identity_enabled or settings.production_identity_configured, "identity_notice": "Local development identities only. Do not expose this instance publicly." if settings.local_identity_enabled else "Verified JWT identity required", "model_configured": service.model_configured, "provider": provider, "presence_ttl_seconds": settings.presence_ttl_seconds, "automatic_response_delay_seconds": settings.response_delay_seconds, "tenant_mode": "single_tenant_database", "reminders_delivery": "in_app_only", "development_usernames": ["DEV-AAA", "DEV-BBB", "DEV-CCC"] if settings.local_identity_enabled else []}


@router.get("/bootstrap")
def bootstrap(owner: OwnerIdentity):
    return service.bootstrap(owner)


@router.get("/people")
def people(owner: OwnerIdentity, q: str = Query("", max_length=100)):
    return service.people(owner, q)


@router.post("/chats")
def create_chat(data: CreateChat, owner: OwnerIdentity):
    return service.create_chat(owner, **data.model_dump())


@router.patch("/chats/{chat_id}")
def rename_chat(chat_id: str, data: ChatTitleInput, owner: OwnerIdentity):
    return service.rename_chat(owner, chat_id, data.title)


@router.put("/chats/{chat_id}/messages/{message_id}/reactions")
def set_message_reaction(chat_id: str, message_id: str, data: MessageReactionInput, owner: OwnerIdentity):
    return service.set_message_reaction(owner, chat_id, message_id, data.emoji, data.active, data.client_request_key)


@router.get("/chats/{chat_id}")
def chat_detail(chat_id: str, owner: OwnerIdentity, limit: int = Query(100, ge=1, le=200), before_seq: int | None = Query(None, ge=1)):
    return service.chat_detail(owner, chat_id, limit, before_seq)


@router.get("/chats/{chat_id}/summary")
def chat_summary(chat_id: str, owner: OwnerIdentity):
    with service.transaction() as db:
        chat, member = service.membership(db, owner, chat_id)
        return service.chat_json(db, chat, member)


@router.post("/chats/{chat_id}/members")
def add_members(chat_id: str, data: AddMembers, owner: OwnerIdentity):
    return service.add_members(owner, chat_id, **data.model_dump())


@router.post("/chats/{chat_id}/messages")
def send_message(chat_id: str, data: SendMessage, owner: OwnerIdentity):
    return service.send_message(owner, chat_id, data.body, data.client_request_key, data.reply_to_message_id, data.reply_for_me, data.attachment_ids)


@router.post("/chats/{chat_id}/reply-requests")
def request_reply(chat_id: str, data: RequestReply, owner: OwnerIdentity):
    return service.manual_request(owner, chat_id, data.message_id, data.target_twin_id, data.client_request_key)


@router.post("/reply-requests/{request_id}/cancel")
async def cancel(request_id: str, owner: OwnerIdentity):
    return await service.cancel_request(owner, request_id)


@router.post("/reply-requests/{request_id}/retry")
def retry(request_id: str, data: Retry, owner: OwnerIdentity):
    return service.retry_request(owner, request_id, data.client_request_key)


@router.post("/chats/{chat_id}/automatic-conversations/{chain_id}/continue")
def continue_automatic_conversation(chat_id: str, chain_id: str, data: ContinueAutomaticConversation, owner: OwnerIdentity):
    return service.continue_automatic_conversation(owner, chat_id, chain_id, data.client_request_key, data.additional_replies, data.expected_allowance)


@router.post("/presence")
async def presence(data: PresenceInput, owner: OwnerIdentity):
    return await service.update_presence(owner, **data.model_dump())


@router.post("/chats/{chat_id}/read")
def read(chat_id: str, data: ReadMessages, owner: OwnerIdentity):
    return service.read(owner, chat_id, data.message_ids)


@router.patch("/chats/{chat_id}/settings")
def patch_chat_settings(chat_id: str, data: ChatSettingsInput, owner: OwnerIdentity):
    values = data.model_dump(exclude_unset=True)
    if any(values.get(key) is None for key in values if key != "prompt_override"):
        raise HTTPException(400, "Only prompt_override may be null")
    return service.patch_chat_settings(owner, chat_id, values)


@router.get("/settings")
def get_settings(owner: OwnerIdentity):
    return service.owner_settings(owner)


@router.patch("/settings")
def patch_settings(data: OwnerSettingsInput, owner: OwnerIdentity):
    return service.owner_settings(owner, data.default_reply_prompt)


@router.get("/chats/{chat_id}/activity")
def activity(chat_id: str, owner: OwnerIdentity, limit: int = Query(50, ge=1, le=200), before: str | None = None):
    import json
    try:
        cursor = json.loads(before) if before else None
        if cursor is not None:
            if not isinstance(cursor, list) or len(cursor) != 3 or not all(isinstance(item, str) for item in cursor):
                raise ValueError("Invalid cursor")
            cursor[0] = datetime.fromisoformat(cursor[0])
    except (ValueError, TypeError):
        raise HTTPException(422, "Activity cursor must contain timestamp, session_id and run_id") from None
    return service.activity_page(owner, chat_id, limit, cursor)


@router.get("/chats/{chat_id}/activity/{session_id}/{run_id}")
async def activity_detail(chat_id: str, session_id: str, run_id: str, owner: OwnerIdentity, limit: int = Query(100, ge=1, le=200), after_seq: int = Query(-1, ge=-1)):
    return await service.activity(owner, chat_id, session_id, run_id, limit, after_seq)


@router.post("/approvals/{approval_id}/decision")
async def decide(approval_id: str, data: Decision, owner: OwnerIdentity):
    return await service.decide_approval(owner, approval_id, data.decision)


@router.get("/chats/{chat_id}/workspace")
def workspace(chat_id: str, owner: OwnerIdentity, path: str = ""):
    return service.workspace(owner, chat_id, path)


@router.post("/chats/{chat_id}/attachments")
async def upload_attachment(chat_id: str, request: Request, owner: OwnerIdentity, name: str = Query(min_length=1, max_length=200)):
    from .attachments import MAX_ATTACHMENT_BYTES
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, "Attachments are limited to 20 MiB")
    return await service.db_call("upload_attachment", owner, chat_id, name, bytes(data), request.headers.get("content-type", "application/octet-stream"))


@router.get("/chats/{chat_id}/attachments/{attachment_id}")
def download_attachment(chat_id: str, attachment_id: str, owner: OwnerIdentity):
    path, info = service.attachment_file(owner, chat_id, attachment_id)
    return FileResponse(path, filename=info["name"], media_type=info["mime_type"], content_disposition_type="inline" if info["kind"] == "image" else "attachment", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store", "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.post("/chats/{chat_id}/workspace/files")
async def upload_workspace(chat_id: str, request: Request, owner: OwnerIdentity, name: str = Query(min_length=1, max_length=200)):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(413, "Workspace files are limited to 20 MiB")
    return await service.db_call("upload_workspace_file", owner, chat_id, name, bytes(data))


@router.get("/chats/{chat_id}/workspace/files/{node_id}")
def download_workspace(chat_id: str, node_id: str, owner: OwnerIdentity):
    path = service.workspace_file(owner, chat_id, node_id)
    return FileResponse(path, filename=path.name, media_type="application/octet-stream", headers={"X-Content-Type-Options": "nosniff"})


@router.get("/chats/{chat_id}/workspace/files/{node_id}/preview")
def preview_workspace(chat_id: str, node_id: str, owner: OwnerIdentity):
    path = service.workspace_file(owner, chat_id, node_id)
    if path.stat().st_size > 1024 * 1024 or path.suffix.casefold() not in {".txt", ".md", ".json", ".csv", ".log", ".py", ".ts", ".tsx", ".yaml", ".yml"}:
        raise HTTPException(415, "Preview supports text files up to 1 MiB; download this file to open it")
    return PlainTextResponse(path.read_text(encoding="utf-8", errors="replace"), headers={"X-Content-Type-Options": "nosniff"})


@router.get("/chats/{chat_id}/tracking")
def tracking_index(chat_id: str, owner: OwnerIdentity):
    return service.tracking_index(owner, chat_id)


@router.get("/chats/{chat_id}/tracking/{topic_id}")
async def get_tracking(chat_id: str, topic_id: str, owner: OwnerIdentity):
    return await service.tracking_action("get_tracking", owner, chat_id, {"id": topic_id})


@router.post("/chats/{chat_id}/tracking")
async def create_tracking(chat_id: str, data: TrackingCreate, owner: OwnerIdentity):
    return await service.tracking_action("create_tracking", owner, chat_id, data.model_dump(), True)


@router.patch("/chats/{chat_id}/tracking/{topic_id}")
async def update_tracking(chat_id: str, topic_id: str, data: TrackingPatch, owner: OwnerIdentity):
    return await service.tracking_action("update_tracking", owner, chat_id, {**data.model_dump(exclude_unset=True), "id": topic_id})


@router.delete("/chats/{chat_id}/tracking/{topic_id}")
async def delete_tracking(chat_id: str, topic_id: str, owner: OwnerIdentity):
    return await service.tracking_action("delete_tracking", owner, chat_id, {"id": topic_id})


@router.post("/chats/{chat_id}/tracking/{topic_id}/updates")
async def update_tracking_chain(chat_id: str, topic_id: str, data: TrackingUpdateInput, owner: OwnerIdentity):
    return await service.tracking_action("add_tracking_update", owner, chat_id, {**data.model_dump(), "id": topic_id})


@router.post("/reminders")
def create_reminder(data: ReminderCreate, owner: OwnerIdentity):
    if data.due_at and data.due_at.tzinfo is None:
        raise HTTPException(400, "due_at must include a timezone")
    return service.create_reminder(owner, **data.model_dump())


@router.patch("/reminders/{reminder_id}")
def update_reminder(reminder_id: str, data: ReminderPatch, owner: OwnerIdentity):
    return service.update_reminder(owner, reminder_id, data.done)


@router.websocket("/ws")
async def websocket(socket: WebSocket):
    from .websocket import websocket_endpoint
    await websocket_endpoint(socket)
