"""One assembly boundary for main runs, recovery and background workers."""
from copy import deepcopy
from pathlib import Path

from agent_runtime.execution import LocalExecutionBackend, create_execution_tools
from agent_runtime.memory import create_memory_tools
from agent_runtime.skills import create_skill_tools

from . import credentials


class TwinExecutionBackend(LocalExecutionBackend):
    """Reuse SDK execution and private home/tmp, with the Twin's existing workspace."""
    def __init__(self, identity, workspace, *, directory=None):
        from .config import ROOT
        super().__init__(identity, directory or ROOT / "data" / "runtime-execution")
        self.cwd = str(Path(workspace).resolve())


async def assemble(service, session, context):
    from . import personal
    request_id = context.get("request_id") or context.get("origin_request_id")
    owner_id = context["owner_id"]
    if request_id or context.get("private_generation") is not None:
        await service.db_call("authorize_runtime_generation", context)
    # Never trust inherited flags: recheck product ownership at every factory.
    authorized = await service.db_call(owner_execution_allowed, service, owner_id, request_id)
    frozen = await service.db_call(personal.get_execution_config, request_id)
    legacy = bool(frozen and frozen.get("version") == 0) or (frozen is None and (
        getattr(session, "resumable", False) or getattr(session, "subagent_creation", None)))
    if legacy:
        # An unfinished pre-feature run must keep its old tool contract. New
        # prompts in an idle session still freeze the current configuration.
        frozen = {"version": 0, "skills": [], "credentials": [], "execution_enabled": False}
    elif frozen is None:
        workspace = await service.db_call(personal.resolve_twin_workspace, owner_id)
        catalog = []
        if authorized and credentials.configured():
            catalog = [{"ref": info.ref, "description": info.description, "env_names": list(info.env_names)}
                       for info in await credentials.make_secret_store(session.info).list()]
        frozen = await service.db_call(personal.freeze_execution_config, request_id, {
            "version": 1, "workspace": str(workspace),
            "skills": await personal.build_skill_config(owner_id),
            "credentials": catalog, "execution_enabled": authorized,
            "profile": {"soul": (soul := await service.db_call(personal.get_soul, owner_id))["content"],
                        "soul_version": soul["version"]},
        })
    config = deepcopy(frozen)
    enabled = bool(authorized and config.get("execution_enabled"))
    tools = []
    if config.get("skills"):
        store = await service.db_call(personal.create_enabled_skill_store, owner_id, config["skills"])
        tools.extend(create_skill_tools(store=store))
    memory_index = None
    if enabled:
        secret_store = credentials.FrozenSecretStore(credentials.make_secret_store(session.info), config.get("credentials", [])) if config.get("credentials") else None
        backend = TwinExecutionBackend(session.info, config["workspace"])
        tools.extend(await create_execution_tools(session=session, backend=backend,
            secrets=secret_store, credential_refs=[item["ref"] for item in config.get("credentials", [])]))
    # Every context uses the same owner store and its existing Git CAS/reset guard.
    if authorized and (enabled or legacy and context.get("chat", {}).get("kind") == "private"):
        store = await service.db_call(personal.make_memory_store, session.info)
        if store is not None:
            tools.extend(create_memory_tools(session=session, store=store))
            memory = await store.read("MEMORY")
            memory_index = memory.to_dict() if memory else None
    saved = getattr(session, "snapshot", {}).get("tools") if getattr(session, "resumable", False) else None
    if saved is not None:
        names = {tool["name"] for tool in saved}
        tools = [tool for tool in tools if tool.name in names]
    return tools, {**context, "execution_config": config, "execution_enabled": enabled}, memory_index


def owner_execution_allowed(service, owner_id, request_id):
    from fastapi import HTTPException
    try:
        with service.transaction() as db:
            credentials.runtime_origin(service, db, owner_id, request_id)
        return True
    except HTTPException as exc:
        if exc.status_code == 403:
            return False
        raise
