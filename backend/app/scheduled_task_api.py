"""Owner-only schedule management; task occurrences use the existing private reply lane."""
from fastapi import APIRouter, Query
from pydantic import Field, StrictBool

from . import scheduled_tasks
from .api import Input, OwnerIdentity
from .service import service

router = APIRouter(prefix="/api/me/twin/scheduled-tasks")


class CreateTask(Input):
    title: str = Field(min_length=1, max_length=256)
    instructions: str = Field(min_length=1, max_length=20000)
    timezone: str = Field(min_length=1, max_length=80)
    schedule: dict


class UpdateTask(Input):
    title: str | None = Field(None, min_length=1, max_length=256)
    instructions: str | None = Field(None, min_length=1, max_length=20000)
    timezone: str | None = Field(None, min_length=1, max_length=80)
    schedule: dict | None = None
    enabled: StrictBool | None = None


class RunNow(Input):
    idempotency_key: str = Field(min_length=1, max_length=160)


@router.get("")
async def list_tasks(owner: OwnerIdentity):
    return await scheduled_tasks.list_tasks_async(service, owner)


@router.post("")
async def create_task(data: CreateTask, owner: OwnerIdentity):
    return await service.db_call(scheduled_tasks.write, service, owner, values=data.model_dump())


@router.patch("/{task_id}")
async def update_task(task_id: str, data: UpdateTask, owner: OwnerIdentity):
    return await service.db_call(scheduled_tasks.write, service, owner, task_id, data.model_dump(exclude_unset=True))


@router.delete("/{task_id}")
async def delete_task(task_id: str, owner: OwnerIdentity):
    return await scheduled_tasks.delete_async(service, owner, task_id)


@router.post("/{task_id}/run-now")
async def run_now(task_id: str, data: RunNow, owner: OwnerIdentity):
    return await scheduled_tasks.run_now_async(service, owner, task_id, data.idempotency_key)


@router.get("/{task_id}/history")
async def history(task_id: str, owner: OwnerIdentity, limit: int = Query(20, ge=1, le=100)):
    return await service.db_call(scheduled_tasks.history, service, owner, task_id, limit)
