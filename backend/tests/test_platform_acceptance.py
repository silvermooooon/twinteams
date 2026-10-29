"""Independent platform participant boundaries; isolated PG and real SDK, offline model."""

import asyncio
import json
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from duet_test_support import request_duet_reply as receiver_request
from fastapi import HTTPException
from sqlalchemy import func, select
from test_memory_dream import product as product  # noqa: PLC0414

from app.models import (
    CausalChain,
    Chat,
    ContextManifest,
    Membership,
    Message,
    ProductOperation,
    ReplyRequest,
    now,
)
from app.runtime_bridge import RuntimeBridge


def append(product, chat_id, owner_id, body):
    with product.transaction() as db:
        chain = product.new_causal_chain(db, owner_id)
        return product.append_message(
            db, db.get(Chat, chat_id), author_id=owner_id.replace("owner-", "twin-") if chat_id.startswith("duet-") else owner_id, kind="twin" if chat_id.startswith("duet-") else "human", body=body, causal_chain_id=chain.id
        ).id


def origin(product):
    source = append(
        product, "duet-alex-maya", "owner-maya", "Please coordinate this independent task " + uuid4().hex
    )
    request = receiver_request(product,
        "owner-maya", "duet-alex-maya", source, "twin-alex", uuid4().hex
    )
    return request, product.get_runtime_context(request["id"])


def call(product, context, name, arguments, operation_id=None):
    return asyncio.run(
        product.runtime_tool(
            name, arguments, {**context, "operation_id": operation_id or uuid4().hex}
        )
    )


def test_cross_chat_publication_is_new_text_not_a_history_grant(product):
    append(product, "private-alex", "owner-alex", "PRIVATE_HISTORY_MUST_STAY_OWNER_ONLY")
    request, context = origin(product)
    from duet_test_support import approved_chat
    target = approved_chat(product, "duet-alex-jordan", ["owner-alex", "owner-jordan"])
    with product.transaction() as db:
        before = db.scalar(
            select(func.count())
            .select_from(ReplyRequest)
            .where(ReplyRequest.chat_id == "private-alex")
        )
    args = {
        "chat_id": "private-alex",
        "body": "Private progress report for the owner.",
        "source_message_ids": [],
    }
    result = call(product, context, "send_chat_message", args, "owner-report")
    assert (
        result["sent"]
        and result["author_id"] == "twin-alex"
        and result["causal_chain_id"] == request["causal_chain_id"]
    )
    assert (
        call(product, context, "send_chat_message", args, "owner-report")["message_id"]
        == result["message_id"]
    )
    with product.transaction() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ReplyRequest)
                .where(ReplyRequest.chat_id == "private-alex")
            )
            == before
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProductOperation)
                .where(ProductOperation.operation_id == "owner-report")
            )
            == 1
        )
    shared = call(
        product,
        context,
        "send_chat_message",
        {"chat_id": target, "body": "New report prepared for CCC.", "source_message_ids": []},
    )
    assert shared["sent"]
    visible = product.chat_detail("owner-jordan", target)
    assert any(message["id"] == shared["message_id"] for message in visible["messages"])
    assert "PRIVATE_HISTORY_MUST_STAY_OWNER_ONLY" not in str(visible)
    forbidden_source = append(
        product, "private-maya", "owner-maya", "Another owner's private source"
    )
    with pytest.raises(HTTPException):
        call(
            product,
            context,
            "send_chat_message",
            {"chat_id": target, "body": "This must fail", "source_message_ids": [forbidden_source]},
        )
    with pytest.raises(HTTPException) as error:
        product.chat_detail("owner-jordan", "duet-alex-maya")
    assert error.value.status_code == 403
    for forbidden in ("private-maya", "private-jordan"):
        with pytest.raises(HTTPException):
            call(
                product,
                context,
                "send_chat_message",
                {"chat_id": forbidden, "body": "Must be denied", "source_message_ids": []},
            )
        with pytest.raises(HTTPException):
            call(product, context, "history", {"chat_id": forbidden})


def test_private_read_is_audited_and_normal_group_output_stays_conservative(product):
    private_id = append(product, "private-alex", "owner-alex", "PRIVATE_READ_REFERENCE_MARKER")
    request, context = origin(product)
    discovered = call(product, context, "list_authorized_chats", {})["chats"]
    assert any(row["id"] == "private-alex" and row["is_owner_private"] for row in discovered)
    read = call(product, context, "history", {"chat_id": "private-alex", "limit": 10})
    assert any(row["id"] == private_id for row in read["messages"])
    with product.transaction() as db:
        sources = db.get(ContextManifest, context["context_manifest_id"]).sources
        assert any(
            row.get("kind") == "chat_context" and row.get("chat_id") == "private-alex"
            for row in sources
        )
    output_id = product.publish_runtime_output(
        request["id"], "Prepared output after private reference read.",
        request["session_id"], "sdk-run", "sdk-event", 7,
    )
    for owner in ("owner-alex", "owner-maya"):
        assert any(row["id"] == output_id for row in product.chat_detail(owner, "duet-alex-maya")["messages"])
    with product.transaction() as db:
        assert any(row.get("chat_id") == "private-alex" for row in db.get(ContextManifest, context["context_manifest_id"]).sources)
        assert db.get(Message, output_id).request_id == request["id"]
        assert db.scalar(select(func.count()).select_from(ReplyRequest).where(ReplyRequest.source_message_id == output_id)) == 1
    with pytest.raises(HTTPException) as denied:
        product.chat_detail("owner-maya", "private-alex")
    assert denied.value.status_code == 403
    assert "PRIVATE_READ_REFERENCE_MARKER" not in str(product.chat_detail("owner-maya", "duet-alex-maya"))


@pytest.mark.parametrize(
    "change", ["source_withdrawn", "source_membership_revoked", "private_reset", "cancel"]
)
def test_late_cross_chat_send_revalidates_all_consumed_sources(product, change):
    private_id = append(product, "private-alex", "owner-alex", "Source can be revoked")
    request, context = origin(product)
    call(product, context, "history", {"chat_id": "private-alex"})
    if change == "private_reset":
        product.personal_reset("owner-alex", uuid4().hex, 0)
    elif change == "cancel":
        product._cancel_intent("owner-maya", request["id"])
    else:
        with product.transaction() as db:
            if change == "source_withdrawn":
                db.get(Message, private_id).state = "withdrawn"
            else:
                db.scalar(
                    select(Membership).where(
                        Membership.chat_id == "duet-alex-maya", Membership.owner_id == "owner-alex"
                    )
                ).left_at = now()
    with pytest.raises(HTTPException):
        call(
            product,
            context,
            "send_chat_message",
            {
                "chat_id": "duet-team",
                "body": "Late report must fail.",
                "source_message_ids": [private_id],
            },
        )
    assert not any(
        row["body"] == "Late report must fail."
        for row in product.chat_detail("owner-maya", "duet-team")["messages"]
    )


def test_group_actual_sdk_discovers_private_reads_and_reports_without_private_execution(product):
    append(product, "private-alex", "owner-alex", "SDK_PRIVATE_REFERENCE_MARKER")
    request, _ = origin(product)

    async def run():
        runtime = RuntimeBridge(product)
        await runtime.start()
        product.bind_runtime(runtime)
        captured, available = [], []

        def offline(model, context, options):
            captured.append(context.messages)
            available.append(
                {getattr(tool, "name", None) or tool.get("name") for tool in context.tools}
            )
            position = len(captured)
            source_ids = []
            if position == 3:
                result_message = next(
                    row for row in reversed(context.messages) if row.get("role") == "toolResult"
                )
                read_result = json.loads(result_message["content"][0]["text"])
                source_ids = [
                    row["id"]
                    for row in read_result["messages"]
                    if "SDK_PRIVATE_REFERENCE_MARKER" in row["body"]
                ]
                assert source_ids, (
                    "The send must cite IDs actually returned by the cross-chat history tool"
                )
            plan = [
                ("list_authorized_chats", {}),
                ("history", {"chat_id": "private-alex", "limit": 10}),
                (
                    "send_chat_message",
                    {
                        "chat_id": "private-alex",
                        "body": "SDK owner progress report.",
                        "source_message_ids": source_ids,
                    },
                ),
            ]
            content = (
                [
                    {
                        "type": "toolCall",
                        "id": f"platform-{position}",
                        "name": plan[position - 1][0],
                        "arguments": plan[position - 1][1],
                    }
                ]
                if position <= len(plan)
                else [{"type": "text", "text": "Conservative group output after private read."}]
            )
            stream = AssistantMessageEventStream()
            stream.push(
                {
                    "type": "done",
                    "message": assistant_message(
                        model,
                        content=content,
                        stopReason="toolUse" if position <= len(plan) else "stop",
                    ),
                }
            )
            return stream

        runtime.models.register_api("offline-regression", offline)
        try:
            with product.transaction() as db:
                before = db.scalar(
                    select(func.count())
                    .select_from(ReplyRequest)
                    .where(ReplyRequest.chat_id == "private-alex")
                )
                spent = db.get(CausalChain, request["causal_chain_id"]).spent
            await product.execute_request(request["id"])
            assert product.get_request(request["id"])["status"] == "completed"
            assert len(captured) == 4 and "SDK_PRIVATE_REFERENCE_MARKER" in str(captured[2])
            assert all(
                not names
                & {
                    "bash",
                    "read",
                    "write",
                    "edit",
                    "request_credential",
                    "memory_read",
                    "memory_write",
                }
                for names in available
            )
            reports = [
                row
                for row in product.chat_detail("owner-alex", "private-alex")["messages"]
                if row["body"] == "SDK owner progress report."
            ]
            assert (
                len(reports) == 1
                and reports[0]["author_id"] == "twin-alex"
                and reports[0]["causal_chain_id"] == request["causal_chain_id"]
            )
            public = [row for row in product.chat_detail("owner-maya", "duet-alex-maya")["messages"]
                      if row["body"] == "Conservative group output after private read."]
            assert len(public) == 1 and public[0]["request_id"] == request["id"]
            with pytest.raises(HTTPException) as denied:
                product.chat_detail("owner-maya", "private-alex")
            assert denied.value.status_code == 403
            with product.transaction() as db:
                assert (
                    db.scalar(
                        select(func.count())
                        .select_from(ReplyRequest)
                        .where(ReplyRequest.chat_id == "private-alex")
                    )
                    == before
                )
                assert db.get(CausalChain, request["causal_chain_id"]).spent == spent + 1
                assert any(source.get("chat_id") == "private-alex" for source in
                           product.manifest_provenance(db, request["id"]))
        finally:
            await runtime.close()

    asyncio.run(run())


def test_receiver_retries_preserve_original_chains_without_spending_send_budget(product):
    old = []
    for _ in range(7):
        request, _ = origin(product)
        product.fail_request(request["id"], "Offline old failure")
        old.append(request)
    _, context = origin(product)
    with product.transaction() as db:
        spent = {row["causal_chain_id"]: db.get(CausalChain, row["causal_chain_id"]).spent for row in old}
        before = db.scalar(select(func.count()).select_from(ReplyRequest))
    accepted = []
    for index, previous in enumerate(old):
        operation = f"retry-platform-{index}"
        result = call(product, context, "retry_reply_request", {"id": previous["id"]}, operation)
        assert call(product, context, "retry_reply_request", {"id": previous["id"]}, operation) == result
        assert result["causal_chain_id"] == previous["causal_chain_id"]
        with product.transaction() as db:
            assert db.get(ReplyRequest, result["id"]).generation == 1
        accepted.append(result["id"])
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReplyRequest)) == before + 7
        assert all(db.get(CausalChain, chain).spent == count for chain, count in spent.items())
        assert len(set(accepted)) == 7
        from sqlalchemy import text
        assert db.execute(text("SELECT count(*) FROM agent_model_usage")).scalar() == 0
    with pytest.raises(HTTPException) as denied:
        product.retry_request("owner-jordan", old[-1]["id"], uuid4().hex)
    assert denied.value.status_code == 403



@pytest.mark.parametrize("phase", ["model", "tools"])
def test_old_platform_tool_contract_resumes_on_real_sdk_then_new_run_gets_capabilities(
    product, phase
):
    import copy

    from agent_runtime import Agent, AgentTool, AgentToolResult
    from agent_runtime.sessions import DatabaseSession
    from agent_runtime.sessions.base import tool_records

    class ProcessLost(BaseException):
        pass

    async def run():
        runtime = RuntimeBridge(product)
        await runtime.start()
        product.bind_runtime(runtime)
        _request, context = origin(product)
        # Model the pre-upgrade handoff window: SDK journal exists before the
        # product has bound its legacy session. New task identities stay strict.
        with product.transaction() as db:
            db.get(ReplyRequest, _request["id"]).session_id = None
        session_id = "legacy-contract-" + uuid4().hex
        session = DatabaseSession(
            runtime.store, tenant_id="local", user_id="owner-alex", session_id=session_id
        )
        legacy_names = {
            "history",
            "list_authorized_chats",
            "send_chat_message",
            "list_tracking",
            "get_tracking",
            "create_tracking",
            "update_tracking",
            "delete_tracking",
            "add_tracking_update",
            "create_approval",
            "execute_approved_action",
            "get_approval",
            "save_summary",
            "create_reminder",
        }
        tools = []
        for current in runtime._tools(context, session):
            if current.name not in legacy_names:
                continue
            tool = copy.copy(current)
            tool.description = "Legacy platform declaration for " + tool.name
            tool.parameters = copy.deepcopy(current.parameters)
            tool.parameters["properties"].pop(
                "chat_id", None
            ) if tool.name != "send_chat_message" else None
            tool.version = "legacy-platform-v1"
            tools.append(tool)
        # New runs no longer advertise save_summary. Freeze its actual old schema
        # explicitly so this remains a fourteen-tool legacy recovery acceptance.
        summary_parameters = {
            "type": "object",
            "properties": {
                "body": {"type": "string"},
                "source_message_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 500},
            },
            "required": ["body", "source_message_ids"],
            "additionalProperties": False,
        }

        async def legacy_summary(call_id, arguments, signal, on_update):
            signal.throw_if_aborted()
            value = await product.runtime_tool("save_summary", arguments,
                {**context, "operation_id": session.tool_operation_id(call_id)})
            return AgentToolResult(content=[{"type": "text", "text": json.dumps(value)}], details=value)

        tools.append(AgentTool(
            name="save_summary",
            description="Save a faithful summary in the originating chat only, citing every authorized source ID covered. Cannot select another chat or replace original history.",
            parameters=summary_parameters,
            execute=legacy_summary,
            replay="safe",
            version="legacy-platform-v1",
        ))
        assert len(tools) == 14
        assert {tool.name for tool in tools} == legacy_names
        if phase == "tools":
            send_tool = next(tool for tool in tools if tool.name == "send_chat_message")
            original = send_tool.execute

            async def commit_then_crash(*args):
                await original(*args)
                raise ProcessLost()

            send_tool.execute = commit_then_crash
        entered = asyncio.Event()

        async def blocked(model, ctx, options):
            entered.set()
            await asyncio.Event().wait()

        initial_calls = []

        def response(model, ctx, options):
            initial_calls.append(ctx.messages)
            if len(initial_calls) > 1:
                raise AssertionError(
                    "Legacy tool never reached crash boundary: " + str(ctx.messages[-1])
                )
            stream = AssistantMessageEventStream()
            content = [
                {
                    "type": "toolCall",
                    "id": "legacy-send",
                    "name": "send_chat_message",
                    "arguments": {
                        "chat_id": "private-alex",
                        "body": "Legacy send committed exactly once.",
                        "source_message_ids": [],
                    },
                }
            ]
            stream.push(
                {
                    "type": "done",
                    "message": assistant_message(model, content=content, stopReason="toolUse"),
                }
            )
            return stream

        def final(model, ctx, options):
            stream = AssistantMessageEventStream()
            stream.push(
                {
                    "type": "done",
                    "message": assistant_message(
                        model,
                        content=[{"type": "text", "text": "Restored old run."}],
                        stopReason="stop",
                    ),
                }
            )
            return stream

        try:
            first = Agent(
                config=runtime.config,
                models=runtime.models,
                session=session,
                tools=tools,
                stream_fn=blocked if phase == "model" else response,
            )
            if phase == "model":
                task = asyncio.create_task(first.prompt("Old accepted request"))
                await asyncio.wait_for(entered.wait(), 3)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                try:
                    await first.prompt("Old accepted request")
                except ProcessLost:
                    pass
                assert len(initial_calls) == 1, str(initial_calls[-1][-1])
            reopened = await DatabaseSession.open(
                runtime.store, tenant_id="local", session_id=session_id
            )
            assert reopened.resumable and reopened.snapshot["phase"] == phase
            restored_tools = runtime._tools(context, reopened)
            assert [tool.name for tool in restored_tools] == [tool.name for tool in tools]
            assert "set_message_reaction" not in {tool.name for tool in restored_tools}
            assert [tool.description for tool in restored_tools] == [
                tool.description for tool in tools
            ]
            assert all(tool.version == "legacy-platform-v1" for tool in restored_tools)
            assert tool_records(restored_tools) == tool_records(tools)
            restored_summary = next(tool for tool in restored_tools if tool.name == "save_summary")
            assert restored_summary.parameters == summary_parameters
            resumed = Agent(
                config=runtime.config,
                models=runtime.models,
                session=reopened,
                tools=restored_tools,
                stream_fn=final,
            )
            await resumed.resume()
            assert not reopened.resumable
            fresh_tools = runtime._tools(context, reopened)
            assert "set_message_reaction" in {tool.name for tool in fresh_tools}
            assert "save_summary" not in {tool.name for tool in fresh_tools}
            assert "save_summary" not in str(next(tool for tool in fresh_tools if tool.name == "create_approval").parameters)
            assert "get_chat" in {tool.name for tool in fresh_tools} and len(fresh_tools) > len(
                tools
            )
            await Agent(
                config=runtime.config,
                models=runtime.models,
                session=reopened,
                tools=fresh_tools,
                stream_fn=final,
            ).prompt("New independent run")
            if phase == "tools":
                rows = [
                    row
                    for row in product.chat_detail("owner-alex", "private-alex")["messages"]
                    if row["body"] == "Legacy send committed exactly once."
                ]
                assert len(rows) == 1
        finally:
            await runtime.close()

    asyncio.run(run())


def test_saved_unknown_tool_names_do_not_install_an_executor():
    from types import SimpleNamespace

    runtime = RuntimeBridge(SimpleNamespace())
    session = SimpleNamespace(
        resumable=True,
        snapshot={
            "tools": [
                {
                    "name": "universal_admin_http",
                    "description": "Untrusted saved declaration",
                    "parameters": {"type": "object"},
                    "replay": "safe",
                    "version": "old",
                }
            ]
        },
    )
    assert (
        runtime._tools(
            {"owner_id": "owner-alex", "chat": {"id": "duet-team", "kind": "group"}}, session
        )
        == []
    )


@pytest.mark.parametrize(
    "action",
    [
        {"tool": "personal_reset", "arguments": {}},
        {"tool": "execute_approved_action", "arguments": {"id": "another"}},
        {
            "tool": "create_reminder",
            "arguments": {"title": "Never created", "owner_id": "owner-maya"},
        },
        {
            "tool": "rename_chat",
            "arguments": {"chat_id": "duet-team", "title": "Injected", "approved": True},
        },
    ],
)
def test_approval_rejects_uninstalled_and_injected_actions(product, action):
    from app.models import Approval

    _request, context = origin(product)
    context.update(
        runtime_session_id="offline-approval-session", runtime_run_id="offline-approval-run"
    )
    with pytest.raises(HTTPException) as error:
        call(
            product,
            context,
            "create_approval",
            {
                "title": "Reject this",
                "description": "Exact actions only",
                "target": "Owner",
                "impact": "One action",
                "action": action,
            },
        )
    assert error.value.status_code == 400
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(Approval).where(Approval.sdk_reference["type"].as_string().is_(None))) == 0


def test_group_approval_private_inbox_then_actual_sdk_executes_frozen_action_once(product):
    from app.models import Approval

    async def run():
        runtime = RuntimeBridge(product)
        await runtime.start()
        product.bind_runtime(runtime)
        request, context = origin(product)
        def settle_origin(model, ctx, options):
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=[], stopReason="stop")})
            return stream
        runtime.models.register_api("offline-regression", settle_origin)
        await product.execute_request(request["id"])
        settled = product.get_request(request["id"])
        assert settled["status"] == "completed"
        context.update(
            operation_id="approval-exact-platform",
            runtime_session_id=settled["session_id"],
            runtime_run_id=settled["run_id"],
        )
        action = {
            "tool": "create_reminder",
            "arguments": {"chat_id": "duet-team", "title": "Exact approved owner reminder"},
        }
        try:
            approval = await product.runtime_tool(
                "create_approval",
                {
                    "title": "Rename the team",
                    "description": "One exact rename",
                    "target": "Team workspace",
                    "impact": "Shared title changes",
                    "action": action,
                },
                context,
            )
            inbox = product.chat_detail("owner-alex", "private-alex")
            assert any(
                row["id"] == approval["id"] and row["chat_id"] == "duet-alex-maya"
                for row in inbox["approvals"]
            )
            assert not any(
                row["id"] == approval["id"]
                for row in product.chat_detail("owner-maya", "duet-alex-maya")["approvals"]
            )
            with pytest.raises(PermissionError):
                await product.runtime_tool(
                    "execute_approved_action", {"id": approval["id"]}, context
                )
            payload = product.approval_for_execution(
                "owner-alex", "duet-alex-maya", approval["id"]
            )
            await runtime.decide_approval(payload, "approved")
            await product.reconcile_approvals()
            await product.reconcile_approvals()
            with product.transaction() as db:
                row = db.get(Approval, approval["id"])
                continued = db.get(ReplyRequest, row.continuation_request_id)
                assert (
                    continued.chat_id == "duet-alex-maya"
                    and continued.causal_chain_id == request["causal_chain_id"]
                )
                continued_id = continued.id
            calls = []

            def offline(model, ctx, options):
                calls.append(ctx)
                assert not {tool.get("name") for tool in ctx.tools} & {
                    "bash",
                    "request_credential",
                    "memory_write",
                }
                content = (
                    [
                        {
                            "type": "toolCall",
                            "id": "execute-frozen",
                            "name": "execute_approved_action",
                            "arguments": {"id": approval["id"]},
                        }
                    ]
                    if len(calls) == 1
                    else [{"type": "text", "text": "The approved group title was changed."}]
                )
                stream = AssistantMessageEventStream()
                stream.push(
                    {
                        "type": "done",
                        "message": assistant_message(
                            model,
                            content=content,
                            stopReason="toolUse" if len(calls) == 1 else "stop",
                        ),
                    }
                )
                return stream

            runtime.models.register_api("offline-regression", offline)
            await product.execute_request(continued_id)
            assert product.get_request(continued_id)["status"] == "completed" and len(calls) == 2
            assert (
                product.bootstrap("owner-alex")["reminders"][0]["title"]
                == action["arguments"]["title"]
            )
            continued_context = product.get_runtime_context(continued_id, include_completed=True)
            await product.runtime_tool(
                "execute_approved_action", {"id": approval["id"]}, continued_context
            )
            with pytest.raises(HTTPException):
                await product.runtime_tool(
                    "execute_approved_action",
                    {
                        "id": approval["id"],
                        "action": {
                            "tool": "create_reminder",
                            "arguments": {"chat_id": "duet-team", "title": "Tampered"},
                        },
                    },
                    continued_context,
                )
            with product.transaction() as db:
                assert (
                    db.scalar(
                        select(func.count())
                        .select_from(ProductOperation)
                        .where(ProductOperation.operation_id == "approval-effect:" + approval["id"])
                    )
                    == 1
                )
                assert (
                    db.scalar(
                        select(func.count())
                        .select_from(ReplyRequest)
                        .where(ReplyRequest.chat_id == "private-alex")
                    )
                    == 2
                )
            assert len(calls) == 2
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("reset_before_execution", [False, True])
def test_exact_owner_approval_private_retry_grants_one_atomically_and_respects_reset(
    product, reset_before_execution
):
    from app.models import AllowanceGrant, Approval

    old_group = []
    for _ in range(6):
        row, _ = origin(product)
        product.fail_request(row["id"], "Offline target failure")
        old_group.append(row)
    private = product.send_message(
        "owner-alex", "private-alex", "Original private task", uuid4().hex
    )["request"]
    from app import personal

    frozen_config = {
        "version": 1,
        "workspace": str(personal.resolve_twin_workspace("owner-alex")),
        "execution_enabled": True,
        "skills": [],
        "credentials": [],
        "profile": {"soul": "Frozen approved original preferences", "soul_version": "original"},
    }
    personal.freeze_execution_config(private["id"], frozen_config)
    current_soul = personal.get_soul("owner-alex")
    personal.write_soul(
        "owner-alex", "Owner changed preferences after the original task", current_soul["version"]
    )
    product.fail_request(private["id"], "Offline private failure")
    controller, context = origin(product)
    with product.transaction() as db:
        db.get(CausalChain, controller["causal_chain_id"]).spent = 6
    for index, row in enumerate(old_group):
        call(
            product,
            context,
            "retry_reply_request",
            {"id": row["id"]},
            f"consume-controller-{index}",
        )
    with pytest.raises(HTTPException) as denied:
        call(
            product,
            context,
            "retry_reply_request",
            {"id": private["id"]},
            "unapproved-private-retry",
        )
    assert denied.value.status_code == 403

    async def run():
        runtime = RuntimeBridge(product)
        await runtime.start()
        product.bind_runtime(runtime)
        context.update(
            operation_id="approve-private-retry",
            runtime_session_id="group-retry-approval",
            runtime_run_id="group-retry-run",
        )
        try:
            approval = await product.runtime_tool(
                "create_approval",
                {
                    "title": "Retry the existing private task",
                    "description": "Retry one frozen existing task",
                    "target": "Group task",
                    "impact": "Harmless",
                    "action": {"tool": "retry_reply_request", "arguments": {"id": private["id"]}},
                },
                context,
            )
            assert approval["action"] == {
                "tool": "retry_reply_request",
                "arguments": {"id": private["id"]},
            }
            assert (
                "private" in approval["target"].lower()
                and "private execution" in approval["impact"].lower()
            )
            payload = product.approval_for_execution(
                "owner-alex", "duet-alex-maya", approval["id"]
            )
            await runtime.decide_approval(payload, "approved")
            await product.reconcile_approvals()
            with product.transaction() as db:
                continued_id = db.get(Approval, approval["id"]).continuation_request_id
                before = db.scalar(select(func.count()).select_from(ReplyRequest))
            continued_context = product.get_runtime_context(continued_id)
            if reset_before_execution:
                product.personal_reset("owner-alex", uuid4().hex, 0)
                with pytest.raises(HTTPException):
                    await product.runtime_tool(
                        "execute_approved_action", {"id": approval["id"]}, continued_context
                    )
                with product.transaction() as db:
                    chain = db.get(CausalChain, controller["causal_chain_id"])
                    assert chain.spent == chain.allowance == 6
                    assert db.scalar(select(func.count()).select_from(ReplyRequest)) == before
                    assert (
                        db.scalar(
                            select(func.count())
                            .select_from(AllowanceGrant)
                            .where(
                                AllowanceGrant.client_request_key == "approval:" + approval["id"]
                            )
                        )
                        == 0
                    )
            else:
                result = await product.runtime_tool(
                    "execute_approved_action", {"id": approval["id"]}, continued_context
                )
                assert result == await product.runtime_tool(
                    "execute_approved_action", {"id": approval["id"]}, continued_context
                )
                assert result["status"] == "queued"
                assert result["causal_chain_id"] == private["causal_chain_id"]
                with product.transaction() as db:
                    chain = db.get(CausalChain, controller["causal_chain_id"])
                    assert chain.spent == chain.allowance == 6
                    assert db.get(ReplyRequest, private["id"]).execution_config == frozen_config
                    retried = db.get(ReplyRequest, result["id"])
                    assert retried.generation == 1 and retried.execution_config == frozen_config
                    assert db.scalar(select(func.count()).select_from(ReplyRequest)) == before + 1
                    assert not db.scalar(select(AllowanceGrant.id).where(AllowanceGrant.client_request_key == "approval:" + approval["id"]))
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.fixture(autouse=True)
def approved_collaboration_fixtures(product):
    from duet_test_support import seed_collaborations
    seed_collaborations(product)
