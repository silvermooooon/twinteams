"""Independent real factory role checks; disposable PG and one scripted model response."""
import asyncio
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from agent_runtime.sessions import DatabaseSession
from test_credentials import product as product  # noqa: PLC0414

from app import personal
from app.models import CausalChain
from app.runtime_bridge import RuntimeBridge
from app.twin_prompts import platform_prompt


@pytest.mark.parametrize("reply_for_me", [False, True])
def test_actual_main_and_child_factories_keep_coordination_out_of_worker_role(product, monkeypatch, reply_for_me):
    monkeypatch.setenv("TWINTEAMS_AUTOMATIC_REPLY_ALLOWANCE", "6")
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
            worker_system = worker.state.messages[0]["content"]
            worker_mission = platform_prompt(context, worker=True)
            assert worker_mission in worker_system and worker_mission != platform_prompt(context)
            assert platform_prompt(context) not in worker_system
            assert "Reply-for-me instructions:" not in worker_system
            assert "This request is reply-for-me: transform" not in worker_system
            assert child.info.user_id == parent.info.user_id == "owner-alex"
            names = {tool.name for tool in worker.state.tools}
            assert not names & {"spawn_agent", "get_agent", "send_agent_message", "wait_agent", "stop_agent"}
            assert not any(name.startswith("memory_") for name in names)
            assert {"bash", "read", "write", "create_approval", "request_credential"} <= names
            assert child.session_id not in runtime.managers
            assert personal.get_execution_config(request["id"]) == frozen_before
            with product.transaction() as db:
                chain = db.get(CausalChain, request["causal_chain_id"])
                assert (chain.spent, chain.allowance) == budget_before
            assert len(captured) == 1, "factory assembly must not start another model call or delegate"
        finally:
            await runtime.close()

    asyncio.run(run())
