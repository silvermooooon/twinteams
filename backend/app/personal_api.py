"""Authenticated personal pages. Reads work independently of model availability."""
from typing import Literal

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field, StrictBool
from starlette.datastructures import UploadFile

from . import personal
from .api import Input, OwnerIdentity
from .attachments import MAX_ATTACHMENT_BYTES
from .service import fail, service

router = APIRouter(prefix="/api")


class TwinPatch(Input):
    name: str | None = Field(None, min_length=1, max_length=128)
    avatar_preset: Literal["orbit", "bloom", "wave", "sunrise", "mountain", "spark"] | None = None


class SkillPatch(Input):
    enabled: StrictBool


class SoulWrite(Input):
    content: str = Field(max_length=50000)
    expected_version: str | None = Field(max_length=64)


class MemoryWrite(Input):
    content: str = Field(max_length=100000)
    expected_version: str | None = Field(max_length=64)


@router.get("/me/twin/soul")
def get_soul(owner: OwnerIdentity):
    return personal.get_soul(owner)


@router.put("/me/twin/soul")
def put_soul(data: SoulWrite, owner: OwnerIdentity):
    result = personal.write_soul(owner, data.content, data.expected_version)
    return JSONResponse(status_code=409, content={"detail": "soul_conflict", "current": result["current"]}) if result.get("conflict") else result


@router.delete("/me/twin/soul")
def delete_soul(owner: OwnerIdentity, expected_version: str | None = Query(None, max_length=64)):
    result = personal.write_soul(owner, "", expected_version, delete=True)
    return JSONResponse(status_code=409, content={"detail": "soul_conflict", "current": result["current"]}) if result.get("conflict") else result


@router.get("/me/twin/memory/status")
def memory_status(owner: OwnerIdentity):
    return personal.memory_status(owner)


@router.put("/me/twin/memory/entry")
async def write_memory(data: MemoryWrite, owner: OwnerIdentity, key: str = Query(min_length=1, max_length=512)):
    result = await personal.edit_memory(owner, key, content=data.content, expected_version=data.expected_version)
    return JSONResponse(status_code=409, content={"detail": "memory_conflict", "current": result["current"]}) if result.get("conflict") else result


@router.delete("/me/twin/memory/entry")
async def delete_memory(owner: OwnerIdentity, key: str = Query(min_length=1, max_length=512), expected_version: str = Query(min_length=1, max_length=64)):
    result = await personal.edit_memory(owner, key, expected_version=expected_version, delete=True)
    return JSONResponse(status_code=409, content={"detail": "memory_conflict", "current": result["current"]}) if result.get("conflict") else result


@router.get("/me/twin")
def get_twin(owner: OwnerIdentity):
    return personal.get_twin(owner)


@router.patch("/me/twin")
def patch_twin(data: TwinPatch, owner: OwnerIdentity):
    values = data.model_dump(exclude_unset=True)
    if "name" in values and values["name"] is None:
        fail(422, "Duet name cannot be null")
    return personal.patch_twin(owner, values)


@router.get("/actors/{actor_id}")
def get_actor(actor_id: str, owner: OwnerIdentity):
    return personal.get_actor(owner, actor_id)


@router.get("/actors/{actor_id}/avatar")
def actor_avatar(actor_id: str, owner: OwnerIdentity, if_none_match: str | None = Header(None)):
    data, etag = personal.avatar_file(owner, actor_id, if_none_match)
    headers = {"ETag": etag, "Cache-Control": "private, no-cache", "Vary": "Cookie, Authorization",
               "X-Content-Type-Options": "nosniff"}
    return Response(content=data, status_code=304 if data is None else 200,
                    media_type="image/webp", headers=headers)


@router.post("/me/twin/avatar")
async def upload_avatar(request: Request, owner: OwnerIdentity):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > personal.MAX_AVATAR_BYTES:
            fail(413, "Avatar must be at most 5 MiB")
        data.extend(chunk)
    return await service.db_call(personal.set_avatar, owner, bytes(data))


@router.delete("/me/twin/avatar")
def remove_avatar(owner: OwnerIdentity):
    return personal.set_avatar(owner, None)


@router.get("/me/twin/skills")
async def skills(owner: OwnerIdentity):
    return await personal.discover_skills(owner)


@router.post("/me/twin/skills/upload")
async def upload_skills(request: Request, owner: OwnerIdentity):
    name, files = await _read_skill_folder(request)
    return await personal.upload_skill(owner, name, files)


async def _read_skill_folder(request):
    async with request.form() as form:
        files = form.getlist("files")
        if not files or len(files) != len(form.multi_items()) or any(not isinstance(file, UploadFile) for file in files):
            fail(422, "Select one Skill folder")
        paths = []
        contents = []
        size = 0
        for file in files:
            path = file.filename or ""
            parts = path.split("/")
            if (len(parts) < 2 or any(not part or part in {".", ".."} or len(part) > 200
                                      or "\\" in part or any(ord(char) < 32 for char in part)
                                      for part in parts)):
                fail(422, "Invalid Skill folder path")
            paths.append(parts)
            data = await file.read(MAX_ATTACHMENT_BYTES - size + 1)
            size += len(data)
            if size > MAX_ATTACHMENT_BYTES:
                fail(413, "Skill folders are limited to 20 MiB")
            contents.append(data)
        name = paths[0][0]
        personal._validate_skill_name(name)
        if (len(name) > 128 or any(parts[0] != name for parts in paths)
                or [name, "SKILL.md"] not in paths or len({tuple(path) for path in paths}) != len(paths)):
            fail(422, "Select one Skill folder containing SKILL.md")
        path_set = {tuple(path) for path in paths}
        if any(tuple(path[:index]) in path_set for path in paths for index in range(2, len(path))):
            fail(422, "Invalid Skill folder path")
        return name, list(zip(paths, contents))


@router.get("/me/twin/skills/detail")
async def skill_detail(owner: OwnerIdentity, name: str = Query(min_length=1, max_length=128)):
    return await personal.skill_detail(owner, name)


@router.patch("/me/twin/skills/{name}")
async def patch_skill(name: str, data: SkillPatch, owner: OwnerIdentity):
    return await personal.patch_skill(owner, name, data.enabled)


@router.get("/me/twin/memory")
async def memory(owner: OwnerIdentity, limit: int = Query(20, ge=1, le=100), after: str | None = Query(None, max_length=512)):
    store = await service.db_call(personal.own_memory_store, owner)
    if store is None:
        return {"enabled": False, "items": [], "next_cursor": None}
    try:
        page = await store.list_entries(limit=limit, after=after)
    except ValueError:
        fail(422, "Invalid memory cursor")
    return {"enabled": True, "items": [entry.to_dict() for entry in page["items"]], "next_cursor": page["next_cursor"]}


@router.get("/me/twin/memory/search")
async def memory_search(owner: OwnerIdentity, q: str = Query(min_length=1, max_length=1000), limit: int = Query(20, ge=1, le=50)):
    store = await service.db_call(personal.own_memory_store, owner)
    if store is None:
        return {"enabled": False, "items": []}
    try:
        items = await store.search(q, limit)
    except ValueError:
        fail(422, "Memory search requires non-empty text")
    return {"enabled": True, "items": items}


@router.get("/me/twin/memory/entry")
async def memory_entry(owner: OwnerIdentity, key: str = Query(min_length=1, max_length=512)):
    store = await service.db_call(personal.own_memory_store, owner)
    if store is None:
        fail(404, "Memory is not enabled")
    try:
        entry = await store.read(key)
    except ValueError:
        fail(422, "Invalid memory key")
    if entry is None:
        fail(404, "Memory entry not found")
    return entry.to_dict()


@router.get("/me/twin/runtime")
def runtime_status(owner: OwnerIdentity):
    return personal.runtime_status(owner)


class TwinResetInput(Input):
    idempotency_key: str = Field(min_length=1, max_length=160)
    expected_generation: int = Field(ge=0, strict=True)


@router.get("/me/twin/reset")
def reset_status(owner: OwnerIdentity):
    return service.reset_status(owner)


@router.post("/me/twin/reset")
async def reset_twin(data: TwinResetInput, owner: OwnerIdentity):
    from fastapi import HTTPException
    try:
        result = await service.db_call("personal_reset", owner, data.idempotency_key, data.expected_generation)
    except HTTPException as exc:
        if exc.status_code == 409:
            return JSONResponse(status_code=409, content={"detail": "reset_conflict", "current": service.reset_status(owner)})
        raise
    return JSONResponse(status_code=202 if result["status"] == "pending" else 200, content=result)


@router.post("/me/twin/memory/dream")
async def trigger_dream(owner: OwnerIdentity):
    return await service.request_memory_dream(owner)
