"""Reply ordering through the real SDK, product transactions and disposable PostgreSQL."""
import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from agent_runtime.transcript import current_tools
from fastapi import HTTPException
from sqlalchemy import select
from test_memory_dream import product as product  # noqa: PLC0414

from app import questions
from app.models import ChangeOutbox, Message, ReplyRequest
from app.runtime_bridge import RuntimeBridge

OPENING = "I'll check your available conversations and summarize the result."
FINAL = "The conversation check is complete. Your private conversation is available."


def response(model, body="", tool=None, *, arguments=None, error=False):
    content = [{"type": "text", "text": body}] if body else []
    if tool:
        content.append({"type": "toolCall", "id": uuid4().hex, "name": tool, "arguments": arguments or {}})
    stream = AssistantMessageEventStream()
    stream.push({"type": "done", "message": assistant_message(model, content=content,
        stopReason="error" if error else "toolUse" if tool else "stop",
        **({"errorMessage": "Offline provider failed after the opening"} if error else {}))})
    return stream


def owner_request(product):
    return product.send_message("owner-alex", "private-alex", "Check my conversations.", uuid4().hex)["request"]


def replies(product):
    return [message for message in product.chat_detail("owner-alex", "private-alex")["messages"]
            if message["kind"] == "twin"]


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled", "empty_final"])
def test_opening_is_persisted_before_work_and_final_is_a_separate_delivery(product, monkeypatch, outcome):
    async def run():
        request = owner_request(product)
        context = product.get_runtime_context(request["id"])
        initial_revision = product.chat_detail("owner-alex", "private-alex")["revision"]
        runtime = RuntimeBridge(product)
        product.bind_runtime(runtime)
        tool_started, release = asyncio.Event(), asyncio.Event()
        actual_tool = product.runtime_tool
        calls = []

        async def delayed_tool(name, args, context):
            assert name == "list_authorized_chats"
            tool_started.set()
            await release.wait()
            return await actual_tool(name, args, context)

        monkeypatch.setattr(product, "runtime_tool", delayed_tool)

        def offline(model, model_context, options):
            calls.append(model_context)
            offered = {tool["name"] for tool in current_tools(model_context.messages)}
            if len(calls) == 1:
                assert offered == {"start_work"}
                return response(model, OPENING, "start_work")
            assert "list_authorized_chats" in offered
            if len(calls) == 2:
                assert [row["body"] for row in replies(product)] == [OPENING]
                return response(model, tool="list_authorized_chats")
            assert len(calls) == 3
            return response(model, "" if outcome == "empty_final" else FINAL, error=outcome == "failed")

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        task = asyncio.create_task(product.execute_request(request["id"]))
        try:
            await asyncio.wait_for(tool_started.wait(), 10)
            assert not task.done()
            assert product.get_request(request["id"])["status"] == "running"
            opening = replies(product)
            assert [row["body"] for row in opening] == [OPENING]
            detail = product.chat_detail("owner-alex", "private-alex")
            assert detail["revision"] > initial_revision
            with product.transaction() as db:
                assert db.scalar(select(ChangeOutbox.id).where(
                    ChangeOutbox.chat_id == "private-alex", ChangeOutbox.revision == detail["revision"]))
            if outcome == "cancelled":
                await product.cancel_request("owner-alex", request["id"])
                await runtime.signal_stop(product.get_request(request["id"]))
            release.set()
            await asyncio.wait_for(task, 10)
            assert product.get_request(request["id"])["status"] == ("failed" if outcome == "empty_final" else outcome)
            saved = replies(product)
            if outcome == "completed":
                assert [row["body"] for row in saved] == [OPENING, FINAL]
                assert saved[0]["id"] != saved[1]["id"] and saved[0]["seq"] < saved[1]["seq"]
                with product.transaction() as db:
                    keys = list(db.scalars(select(Message.sdk_output_key).where(
                        Message.request_id == request["id"], Message.kind == "twin")))
                assert len(keys) == len(set(keys)) == 2
                result = await runtime.execute(product.get_request(request["id"]), context)
                assert result["body"] == FINAL
                product.complete_request(request["id"], result)
                assert len(calls) == 3 and replies(product) == saved
            elif outcome in {"failed", "empty_final"}:
                assert [row["body"] for row in saved] == [OPENING]
                with product.transaction() as db:
                    error = db.get(ReplyRequest, request["id"]).error
                    assert ("without delivering a final result" if outcome == "empty_final"
                            else "Offline provider failed") in error
            else:
                assert saved == []
        finally:
            release.set()
            await task
            await runtime.close()

    asyncio.run(run())


def test_immediate_answer_is_one_complete_message_without_start_work(product):
    async def run():
        request = owner_request(product)
        runtime = RuntimeBridge(product)
        product.bind_runtime(runtime)
        calls = []

        def offline(model, context, options):
            calls.append(context)
            return response(model, "You are speaking with your own Duet in this private chat.")

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        try:
            await product.execute_request(request["id"])
            assert product.get_request(request["id"])["status"] == "completed"
            assert len(calls) == len(replies(product)) == 1
            assert replies(product)[0]["body"] == "You are speaking with your own Duet in this private chat."
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("tool", ["list_authorized_chats", "start_work"])
def test_tool_without_opening_cannot_start_work(product, monkeypatch, tool):
    async def run():
        request = owner_request(product)
        runtime = RuntimeBridge(product)
        product.bind_runtime(runtime)
        calls = []

        async def forbidden(*args):
            pytest.fail("Work began before a visible opening")

        monkeypatch.setattr(product, "runtime_tool", forbidden)

        def offline(model, context, options):
            calls.append(context)
            if len(calls) == 1:
                return response(model, tool=tool)
            assert len(calls) == 2
            result = next(row for row in reversed(context.messages) if row["role"] == "toolResult")
            assert result["isError"]
            return response(model, "This request needs a conversation lookup.")

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        try:
            await product.execute_request(request["id"])
            assert len(replies(product)) == 1
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("delivery", ["milestone", "question", "file"])
def test_milestones_and_existing_visible_delivery_or_handoff(product, delivery):
    async def run():
        request = owner_request(product)
        context = product.get_runtime_context(request["id"])
        path = Path(context["workspace_path"]) / "checked-result.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Verified fixture result")
        runtime = RuntimeBridge(product)
        product.bind_runtime(runtime)
        calls = []
        milestone = "Your private chat is available. I'll check the remaining conversations."

        def offline(model, model_context, options):
            calls.append(model_context)
            if len(calls) == 1:
                return response(model, OPENING, "start_work")
            if len(calls) == 2:
                if delivery == "question":
                    return response(model, tool="ask_question", arguments={"question": "Which project should I check?"})
                if delivery == "file":
                    return response(model, tool="present_files", arguments={"paths": [str(path)], "caption": "Verified result"})
                return response(model, milestone, "list_authorized_chats")
            assert len(calls) == 3
            result = next(row for row in reversed(model_context.messages) if row["role"] == "toolResult")
            assert not result.get("isError"), result
            return response(model, FINAL if delivery == "milestone" else "")

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        try:
            await product.execute_request(request["id"])
            with product.transaction() as db:
                saved = db.get(ReplyRequest, request["id"])
                assert saved.status == "completed", saved.error
            detail = product.chat_detail("owner-alex", "private-alex")
            if delivery == "milestone":
                assert [row["body"] for row in replies(product)] == [OPENING, milestone, FINAL]
            elif delivery == "question":
                assert detail["questions"][0]["status"] == "pending"
                assert detail["questions"][0]["question"] == "Which project should I check?"
            else:
                assert detail["messages"][-1]["body"] == "Verified result"
                assert len(detail["messages"][-1]["attachments"]) == 1
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("decision", ["Confirm send", "Cancel"])
def test_opening_preserves_prepare_and_confirm_external_sends(product, decision):
    async def run():
        body = "The revised plan is ready."
        request = product.send_message("owner-alex", "private-alex", "Send to Maya: " + body, uuid4().hex)["request"]
        before = product.chat_detail("owner-alex", "group-alex-maya")["messages"]
        runtime = RuntimeBridge(product)
        product.bind_runtime(runtime)
        calls = []

        def offline(model, context, options):
            calls.append(context)
            if len(calls) == 1:
                return response(model, "I'll prepare the message for your confirmation.", "start_work")
            if len(calls) == 2:
                return response(model, tool="reply_as_owner", arguments={"chat_id": "group-alex-maya", "body": body})
            assert len(calls) == 3
            result = next(row for row in reversed(context.messages) if row["role"] == "toolResult")
            assert not result.get("isError"), result
            assert result["details"]["status"] == "confirmation_required"
            return response(model)

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        try:
            await product.execute_request(request["id"])
            assert product.get_request(request["id"])["status"] == "completed"
            assert not product.get_request(request["id"])["background"]
            assert product.chat_detail("owner-alex", "group-alex-maya")["messages"] == before
            question = product.chat_detail("owner-alex", "private-alex")["questions"][0]
            with pytest.raises(HTTPException) as typed:
                questions.answer(product, "owner-alex", question["id"], "Confirm send")
            assert typed.value.status_code == 422
            assert product.chat_detail("owner-alex", "group-alex-maya")["messages"] == before
            questions.answer(product, "owner-alex", question["id"], decision, selected_option=True)
            after = product.chat_detail("owner-alex", "group-alex-maya")["messages"]
            assert len(after) == len(before) + (decision == "Confirm send")
            if decision == "Confirm send":
                assert after[-1]["body"] == body and after[-1]["author_id"] == "owner-alex"
        finally:
            await runtime.close()

    asyncio.run(run())
