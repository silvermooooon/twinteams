"""Authenticated owner-only first-meeting lifecycle; no model invocation."""
from fastapi import APIRouter

from . import onboarding
from .api import OwnerIdentity
from .service import service

router = APIRouter(prefix="/api/me/twin/onboarding")


@router.get("")
async def state(owner: OwnerIdentity):
    return await service.db_call(onboarding.get_state, service, owner)


@router.post("/start")
async def start(owner: OwnerIdentity):
    return await service.db_call(onboarding.start, service, owner)


@router.post("/skip")
async def skip(owner: OwnerIdentity):
    return await service.db_call(onboarding.skip, service, owner)
