"""Independent reaction attribution/ACL/recovery checks; real PG/SDK, offline model."""

import asyncio
import json
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from test_memory_dream import product as product  # noqa: PLC0414

from app import service as service_module
from app.main import app
from app.models import Chat, Membership, Message, now
from app.runtime_bridge import RuntimeBridge


def seed_message(
    product, chat_id="group-team", owner_id="owner-maya", body="Synthetic reaction target"
):
    with product.transaction() as db:
        return product.append_message(
            db, db.get(Chat, chat_id), author_id=owner_id, kind="human", body=body
        ).id


def context_for(product, chat_id="group-team"):
    mid = seed_message(product, chat_id)
    request = product.manual_request("owner-maya", chat_id, mid, "twin-alex", uuid4().hex)
    return request, product.get_runtime_context(request["id"]), mid


def client(username):
    result = TestClient(app)
    assert result.post("/api/auth/login", json={"username": username}).status_code == 200
    return result


def reaction(client, chat_id, message_id, active, *, emoji="👍", key=None, **extra):
    return client.put(
        f"/api/chats/{chat_id}/messages/{message_id}/reactions",
        json={"emoji": emoji, "active": active, "client_request_key": key or uuid4().hex, **extra},
    )


def aggregate(product, owner, chat_id, message_id):
    return next(
        row for row in product.chat_detail(owner, chat_id)["messages"] if row["id"] == message_id
    ).get("reactions", [])


def test_human_reaction_does_not_impersonate_twin_and_replay_does_not_restore_removed_state(
    product,
):
    mid = seed_message(product)
    own, other = client("DEV-AAA"), client("DEV-BBB")
    first = reaction(own, "group-team", mid, True, key="human-add")
    assert first.status_code == 200
    state = first.json()["reactions"]
    assert len(state) == 1 and state[0]["count"] == 1 and state[0]["mine"] is True
    assert [(actor["id"], actor["kind"]) for actor in state[0]["actors"]] == [
        ("owner-alex", "human")
    ]
    assert aggregate(product, "owner-maya", "group-team", mid)[0]["mine"] is False
    assert reaction(own, "group-team", mid, False, key="human-remove").status_code == 200
    assert reaction(own, "group-team", mid, True, key="human-add").status_code == 200
    assert aggregate(product, "owner-alex", "group-team", mid) == []
    assert reaction(other, "group-team", mid, True, actor_id="twin-alex").status_code == 422
    assert reaction(own, "group-team", mid, True, key="conflicting-key").status_code == 200
    assert reaction(own, "group-team", mid, False, key="conflicting-key").status_code == 409


@pytest.mark.parametrize("change", ["withdrawn", "membership", "history_floor", "private_reset"])
def test_reaction_late_and_replayed_operations_recheck_visibility(product, change):
    if change == "private_reset":
        mid = seed_message(product, "private-alex", "owner-alex")
        private = product.send_message(
            "owner-alex", "private-alex", "Existing private task", uuid4().hex
        )["request"]
        context = product.get_runtime_context(private["id"])
        chat_id = "private-alex"
    else:
        _request, context, mid = context_for(product)
        chat_id = "group-team"
    args = {"message_id": mid, "chat_id": chat_id, "emoji": "👍", "active": True}
    first = asyncio.run(
        product.runtime_tool(
            "set_message_reaction", args, {**context, "operation_id": "reaction-before-revoke"}
        )
    )
    assert first["message_id"] == mid
    if change == "private_reset":
        product.personal_reset("owner-alex", uuid4().hex, 0)
    else:
        with product.transaction() as db:
            if change == "withdrawn":
                db.get(Message, mid).state = "withdrawn"
            else:
                member = db.scalar(
                    select(Membership).where(
                        Membership.chat_id == chat_id, Membership.owner_id == "owner-alex"
                    )
                )
                if change == "membership":
                    member.left_at = now()
                else:
                    member.lower_seq = db.get(Message, mid).seq + 1
    for operation in ("reaction-before-revoke", "reaction-late-new"):
        with pytest.raises(HTTPException):
            asyncio.run(
                product.runtime_tool(
                    "set_message_reaction", args, {**context, "operation_id": operation}
                )
            )


def test_actual_sdk_twin_add_remove_keeps_human_state_and_replay_does_not_call_model(product):
    request, _context, mid = context_for(product)
    human = client("DEV-BBB")
    assert reaction(human, "group-team", mid, True).status_code == 200

    async def run():
        runtime = RuntimeBridge(product)
        await runtime.start()
        product.bind_runtime(runtime)
        calls = []

        def offline(model, ctx, options):
            calls.append(ctx.messages)
            if len(calls) == 2:
                result = json.loads(
                    next(row for row in reversed(ctx.messages) if row.get("role") == "toolResult")[
                        "content"
                    ][0]["text"]
                )
                state = result["reactions"][0]
                assert state["count"] == 2 and state["mine"] is False
                assert {(a["id"], a["kind"]) for a in state["actors"]} == {
                    ("owner-maya", "human"),
                    ("twin-alex", "twin"),
                }
            if len(calls) == 3:
                history = json.loads(
                    next(row for row in reversed(ctx.messages) if row.get("role") == "toolResult")[
                        "content"
                    ][0]["text"]
                )
                target = next(row for row in history["messages"] if row["id"] == mid)
                assert {actor["kind"] for actor in target["reactions"][0]["actors"]} == {
                    "human",
                    "twin",
                }
            content = (
                [
                    {
                        "type": "toolCall",
                        "id": "sdk-reaction-" + str(len(calls)),
                        "name": "history" if len(calls) == 2 else "set_message_reaction",
                        "arguments": {}
                        if len(calls) == 2
                        else {"message_id": mid, "emoji": "👍", "active": len(calls) == 1},
                    }
                ]
                if len(calls) <= 3
                else []
            )
            stream = AssistantMessageEventStream()
            stream.push(
                {
                    "type": "done",
                    "message": assistant_message(
                        model, content=content, stopReason="toolUse" if len(calls) <= 3 else "stop"
                    ),
                }
            )
            return stream

        runtime.models.register_api("offline-regression", offline)
        try:
            await product.execute_request(request["id"])
            assert product.get_request(request["id"])["status"] == "completed" and len(calls) == 4
            state = aggregate(product, "owner-alex", "group-team", mid)
            assert (
                state[0]["count"] == 1
                and state[0]["actors"][0]["id"] == "owner-maya"
                and state[0]["mine"] is False
            )
            assert aggregate(product, "owner-maya", "group-team", mid)[0]["mine"] is True
            await product.execute_request(request["id"])
            assert len(calls) == 4
        finally:
            await runtime.close()

    asyncio.run(run())


def test_reaction_projection_uses_one_batch_query_for_a_full_history_page(product):
    ids = [seed_message(product, body=f"Batch target {index}") for index in range(35)]
    own = client("DEV-AAA")
    for mid in ids:
        assert reaction(own, "group-team", mid, True).status_code == 200
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        lowered = statement.lower()
        if lowered.lstrip().startswith("select") and "reaction" in lowered:
            statements.append(lowered)

    event.listen(service_module.engine, "before_cursor_execute", record)
    try:
        detail = product.chat_detail("owner-maya", "group-team", limit=100)
    finally:
        event.remove(service_module.engine, "before_cursor_execute", record)
    assert len(detail["messages"]) == 35 and all(row.get("reactions") for row in detail["messages"])
    assert len(statements) == 1, f"Reaction SELECTs must be bounded per page, got {len(statements)}"


def test_cross_chat_reaction_requires_actual_message_visibility_and_preserves_source_acl(product):
    request, context, _ = context_for(product)
    private = seed_message(product, "private-alex", "owner-alex", "Owner-only source")
    others_private = seed_message(product, "private-maya", "owner-maya", "Other owner's source")

    def call(chat_id, message_id, **extra):
        return asyncio.run(
            product.runtime_tool(
                "set_message_reaction",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "emoji": "👍",
                    "active": True,
                    **extra,
                },
                {**context, "operation_id": uuid4().hex},
            )
        )

    assert call("private-alex", private)["actor_id"] == "twin-alex"
    for target, mid, extra in (
        ("private-maya", others_private, {}),
        ("group-team", private, {}),
        ("private-alex", private, {"actor_id": "owner-maya"}),
    ):
        try:
            call(target, mid, **extra)
        except HTTPException:
            pass
        else:
            raise AssertionError(f"Unauthorized reaction accepted: target={target} extra={extra}")
    # Reading a private reaction target adds the existing private source envelope.
    # It must not make an ordinary group automatic output readable to others.
    output = product.publish_runtime_output(
        request["id"], "Private-derived output", "s", "r", "e", 1
    )
    assert any(
        row["id"] == output for row in product.chat_detail("owner-alex", "group-team")["messages"]
    )
    assert all(
        row["id"] != output for row in product.chat_detail("owner-maya", "group-team")["messages"]
    )


def test_committed_reaction_tool_phase_recovers_without_duplicate_effect(product):
    from agent_runtime import Agent
    from agent_runtime.sessions import DatabaseSession

    class ProcessLost(BaseException):
        pass

    _request, context, mid = context_for(product)

    async def run():
        runtime = RuntimeBridge(product)
        await runtime.start()
        session_id = "reaction-recovery-" + uuid4().hex
        session = DatabaseSession(
            runtime.store, tenant_id="local", user_id="owner-alex", session_id=session_id
        )
        tools = runtime._tools(context, session)
        target = next(tool for tool in tools if tool.name == "set_message_reaction")
        execute = target.execute

        async def commit_then_crash(*args):
            await execute(*args)
            raise ProcessLost()

        target.execute = commit_then_crash
        calls = []

        def response(model, ctx, options):
            calls.append(1)
            assert len(calls) == 1
            stream = AssistantMessageEventStream()
            stream.push(
                {
                    "type": "done",
                    "message": assistant_message(
                        model,
                        content=[
                            {
                                "type": "toolCall",
                                "id": "reaction-crash",
                                "name": "set_message_reaction",
                                "arguments": {"message_id": mid, "emoji": "👍", "active": True},
                            }
                        ],
                        stopReason="toolUse",
                    ),
                }
            )
            return stream

        def final(model, ctx, options):
            stream = AssistantMessageEventStream()
            stream.push(
                {"type": "done", "message": assistant_message(model, content=[], stopReason="stop")}
            )
            return stream

        try:
            with pytest.raises(ProcessLost):
                await Agent(
                    config=runtime.config,
                    models=runtime.models,
                    session=session,
                    tools=tools,
                    stream_fn=response,
                ).prompt("React to this message")
            assert aggregate(product, "owner-alex", "group-team", mid)[0]["count"] == 1
            reopened = await DatabaseSession.open(
                runtime.store, tenant_id="local", session_id=session_id
            )
            assert reopened.resumable and reopened.snapshot["phase"] == "tools"
            before = product.chat_detail("owner-alex", "group-team")["revision"]
            await Agent(
                config=runtime.config,
                models=runtime.models,
                session=reopened,
                tools=runtime._tools(context, reopened),
                stream_fn=final,
            ).resume()
            assert not reopened.resumable and len(calls) == 1
            assert aggregate(product, "owner-alex", "group-team", mid)[0]["count"] == 1
            assert product.chat_detail("owner-alex", "group-team")["revision"] == before
        finally:
            await runtime.close()

    asyncio.run(run())
