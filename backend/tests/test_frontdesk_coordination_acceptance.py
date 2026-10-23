"""Independent real factory role checks; disposable PG and one scripted model response."""
import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from agent_runtime.sessions import DatabaseSession
from agent_runtime.sessions.base import model_record
from test_credentials import product as product  # noqa: PLC0414

from app import personal
from app.models import CausalChain
from app.runtime_bridge import RuntimeBridge
from app.twin_prompts import platform_prompt


@pytest.mark.parametrize("reply_for_me", [False, True])
def test_actual_main_and_child_factories_keep_coordination_out_of_worker_role(product, monkeypatch, reply_for_me):
    monkeypatch.setenv("TWINTEAMS_AUTOMATIC_REPLY_ALLOWANCE", "6")
    monkeypatch.setenv("TWINTEAMS_SUBAGENT_MODEL", "obsolete-worker-only-override")
    captured = []

    async def run():
        runtime = RuntimeBridge(product)

        def offline(model, context, options):
            captured.append(context)
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model,
                content=[{"type": "text", "text": "Synthetic main response only."}], stopReason="stop")})
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        try:
            request = product.send_message("owner-alex", "private-alex", "Synthetic assigned work", uuid4().hex,
                reply_for_me=reply_for_me)["request"]
            context = product.get_runtime_context(request["id"])
            assert (context.get("raw_owner_input") is not None) == reply_for_me
            result = await runtime.execute(product.get_request(request["id"]), context)
            product.complete_request(request["id"], result)
            parent = await DatabaseSession.open(runtime.store, tenant_id=runtime.tenant_id, session_id=result["session_id"])
            main_tools = {row["name"] for row in parent.snapshot["tools"]}
            assert {"spawn_agent", "get_agent", "send_agent_message", "wait_agent", "stop_agent"} <= main_tools
            assert platform_prompt(context) in captured[0].messages[0]["content"]
            frozen_before = personal.get_execution_config(request["id"])
            with product.transaction() as db:
                chain = db.get(CausalChain, request["causal_chain_id"])
                budget_before = chain.spent, chain.allowance
            assert budget_before[1] == 6
            child = DatabaseSession(runtime.store, tenant_id=runtime.tenant_id,
                user_id="owner-alex", session_id="synthetic-role-child-" + uuid4().hex)
            await child.commit("subagent_created", parent_session_id=result["session_id"],
                parent_run_id=result["run_id"], root_run_id=result["run_id"], name="worker",
                task="Complete the assigned synthetic subtask and report facts to the parent",
                operation_id="synthetic-spawn-" + uuid4().hex)
            worker = await runtime._child_factory(child)
            assert worker.state.model.id == runtime.models.get_model().id
            worker_system = worker.state.messages[0]["content"]
            worker_mission = platform_prompt(context, worker=True)
            assert worker_mission in worker_system and worker_mission != platform_prompt(context)
            assert platform_prompt(context) not in worker_system
            assert "Reply-for-me instructions:" not in worker_system
            assert "This request is reply-for-me: transform" not in worker_system
            assert child.info.user_id == parent.info.user_id == "owner-alex"
            names = {tool.name for tool in worker.state.tools}
            assert {"spawn_agent", "get_agent", "send_agent_message", "wait_agent", "stop_agent"} <= names
            assert {"memory_read", "memory_write", "memory_search", "memory_delete"} <= names
            assert {"bash", "read", "write", "create_approval", "request_credential"} <= names
            assert child.session_id in runtime.managers
            assert personal.get_execution_config(request["id"]) == frozen_before
            with product.transaction() as db:
                chain = db.get(CausalChain, request["causal_chain_id"])
                assert (chain.spent, chain.allowance) == budget_before
            assert len(captured) == 1, "factory assembly must not start another model call or delegate"
            # A previously accepted worker model survives deployment even though
            # new workers no longer use a role-specific model override.
            old_model = replace(worker.state.model, id="accepted-old-worker-model")
            await child.commit("run_started", run_id=uuid4().hex, source="subagent",
                root_run_id=result["session_id"] + "/" + result["run_id"],
                inputs=[{"role": "user", "content": "Previously accepted subtask", "timestamp": 0}],
                model=model_record(old_model), options={}, tools=parent.snapshot["tools"],
                tool_execution="parallel", queue_modes={"steering": "one-at-a-time", "follow_up": "one-at-a-time"}, required_hooks=[])
            recovered = await runtime._child_factory(child)
            assert recovered.state.model.id == old_model.id
            assert len(captured) == 1
        finally:
            await runtime.close()

    asyncio.run(run())
