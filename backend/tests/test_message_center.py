"""Message Center receipts use real isolated product storage and SDK execution."""
import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from sqlalchemy import func, select
from test_memory_dream import product as product  # noqa: PLC0414

from app import message_center as center
from app import questions, scheduled_tasks
from app.models import Chat, Message, ReplyRequest, ScheduledTask, now
from app.question_models import QuestionRequest
from app.runtime_bridge import RuntimeBridge, text_content


@pytest.fixture(autouse=True)
def configure_center(monkeypatch):
    monkeypatch.setattr(center, "settings", replace(center.settings, message_center_url="http://center", message_center_token="test"))


def delivery(delivery_id, mode="immediate", owner="owner-maya"):
    return {"delivery_id": delivery_id, "message_id": "message-" + delivery_id,
        "version": 1, "owner_id": owner, "mode": mode,
        "message": {"id": "message-" + delivery_id, "version": 1, "status": "active", "title": delivery_id, "body": "Reference only"}}


def test_atomic_receipt_duplicate_retry_and_digest_snapshot(product):
    item = delivery("first")
    accepted = center.accept(product, item)
    assert center.accept(product, item) == accepted
    with product.transaction() as db:
        request = db.get(ReplyRequest, accepted["request_id"])
        assert request.source == "message_center" and request.background and request.source_message_id is None
        assert db.scalar(select(func.count()).select_from(Message).where(Message.chat_id == "private-maya")) == 0
    product.finish_request(accepted["request_id"], "failed", error="offline")
    retried = product.retry_request("owner-maya", accepted["request_id"], "retry-center")
    assert retried["source"] == "message_center"
    with product.transaction() as db:
        assert db.get(center.CenterDelivery, "first").request_id == retried["id"]
    center.accept(product, delivery("digest-1", "digest"))
    task_id = scheduled_tasks.system_id("owner-maya")
    with product.transaction() as db:
        task = db.get(ScheduledTask, task_id)
        task.next_run_at = now() - timedelta(seconds=1)
    request_id = scheduled_tasks.process_due(product, [task_id])[0]
    assert [item["delivery_id"] for item in product.get_runtime_context(request_id)["system_review"]["message_center"]] == ["digest-1"]
    center.accept(product, delivery("digest-2", "digest"))
    product.finish_request(request_id, "completed")
    with product.transaction() as db:
        assert db.get(center.CenterDelivery, "digest-1").state == "processed"
        assert db.get(center.CenterDelivery, "digest-2").state == "received"


def test_sdk_immediate_silent_report_and_digest(product, monkeypatch):
    monkeypatch.setattr(center, "settings", replace(center.settings, message_center_url="http://center", message_center_token="test"))
    outputs = []
    async def fake_call(method, path, *, params=None, body=None):
        if method == "GET":
            name = path.rsplit("/", 1)[-1].removeprefix("message-")
            return delivery(name)["message"]
        outputs.append((path, body))
        return {"ok": True}
    monkeypatch.setattr(center, "call", fake_call)

    async def exercise():
        runtime = RuntimeBridge(product)
        seen = []
        def offline(model, context, options):
            user = text_content(next(row for row in reversed(context.messages) if row["role"] == "user"))
            seen.append(user)
            content = [{"type": "text", "text": "Useful notice for Maya"}] if '"title": "report"' in user else []
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=content, stopReason="stop")})
            return stream
        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        product.bind_runtime(runtime)
        product.loop = asyncio.get_running_loop()
        try:
            silent = center.accept(product, delivery("silent"))["request_id"]
            report = center.accept(product, delivery("report"))["request_id"]
            await product.execute_request(silent)
            await product.execute_request(report)
            center.accept(product, delivery("digest", "digest"))
            task_id = scheduled_tasks.system_id("owner-maya")
            with product.transaction() as db:
                db.get(ScheduledTask, task_id).next_run_at = now() - timedelta(seconds=1)
            review = scheduled_tasks.process_due(product, [task_id])[0]
            await product.execute_request(review)
            await center.flush_outbox(product)
            with product.transaction() as db:
                assert all(db.get(ReplyRequest, rid).status == "completed" for rid in (silent, report, review))
                assert not list(db.scalars(select(Message).where(Message.request_id == silent)))
                assert [row.body for row in db.scalars(select(Message).where(Message.request_id == report))] == ["Useful notice for Maya"]
                assert all(db.get(center.CenterDelivery, name).state == "processed" for name in ("silent", "report", "digest"))
                assert db.get(center.CenterDelivery, "report").notified
                assert not db.get(center.CenterDelivery, "silent").notified
            assert len(outputs) == 3
            assert any("digest" in value for value in seen)
        finally:
            await runtime.close()
    asyncio.run(exercise())


def test_invitation_question_answer_resumes_and_records_owner_stance(product, monkeypatch):
    item = delivery("invitation")
    item["message"].update(kind="invitation", title="Team picnic", body="Would you like to join the team picnic?")
    responses = []

    async def fake_call(method, path, *, params=None, body=None):
        if method == "GET":
            return item["message"]
        responses.append((path, body))
        return {"ok": True}

    monkeypatch.setattr(center, "call", fake_call)

    async def exercise():
        runtime = RuntimeBridge(product)

        def offline(model, context, options):
            position = max(i for i, row in enumerate(context.messages) if row["role"] == "user")
            user = text_content(context.messages[position])
            results = [row for row in context.messages[position + 1:] if row["role"] == "toolResult"]
            if results:
                assert not results[-1].get("isError"), results[-1]
                content = []
            elif user.startswith("A Message Center delivery"):
                assert "use ask_question" in user and "do not assume their stance" in user
                assert "end without repeating the invitation or question" in user
                content = [{"type": "toolCall", "id": "ask", "name": "ask_question", "arguments": {
                    "question": "Would you like me to tell the organizer you're attending the team picnic?",
                    "options": []}}]
            else:
                assert user.startswith("The owner answered") and '"answer": "Yes"' in user
                content = [{"type": "toolCall", "id": "respond", "name": "respond_center_message", "arguments": {
                    "id": item["message_id"], "response": "attending"}}]
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=content,
                stopReason="toolUse" if content else "stop")})
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        product.bind_runtime(runtime)
        try:
            request_id = center.accept(product, item)["request_id"]
            await product.execute_request(request_id)
            with product.transaction() as db:
                question = db.scalar(select(QuestionRequest))
                assert question and question.owner_id == "owner-maya" and question.status == "pending"
                delivery_row = db.get(center.CenterDelivery, item["delivery_id"])
                assert delivery_row.state == "processed" and delivery_row.notified
                question_id = question.id
            assert responses == []
            questions.answer(product, "owner-maya", question_id, "Yes")
            with product.transaction() as db:
                continuation_id = db.get(QuestionRequest, question_id).continuation_request_id
            assert product.get_runtime_context(continuation_id)["message_center"]["message_id"] == item["message_id"]
            await product.execute_request(continuation_id)
            assert product.get_request(continuation_id)["status"] == "completed"
            assert responses == [("/messages/message-invitation/responses", {
                "owner_id": "owner-maya", "response": "attending", "note": None})]
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_publish_requires_explicit_audience_but_allows_subscriptions():
    schema = next(schema for schema in center.tool_schemas() if schema[0] == "publish_center_message")
    assert "recipients" in schema[3]
    assert "minItems" not in schema[2]["recipients"]


def test_private_prompt_routes_invitations_and_requires_owner_selected_audience():
    from app.twin_prompts import conversation_system

    prompt = conversation_system({"chat": {"kind": "private"}})
    assert "announcements or invitations that collect responses" in prompt
    assert "Ask the owner who to invite" in prompt
    assert "including the owner if selected" in prompt


def test_obsolete_delivery_and_durable_result_retry(product, monkeypatch):
    async def exercise():
        item = delivery("old")
        request_id = center.accept(product, item)["request_id"]
        center.refresh_entries(product, request_id, {"old": {**item["message"], "version": 2}})
        with product.transaction() as db:
            assert db.get(center.CenterDelivery, "old").state == "obsolete"
        current = center.accept(product, delivery("current"))["request_id"]
        product.finish_request(current, "completed")
        async def offline(*args, **kwargs):
            raise OSError("offline")
        monkeypatch.setattr(center, "call", offline)
        with pytest.raises(OSError):
            await center.flush_outbox(product)
        with product.transaction() as db:
            assert not db.get(center.CenterDelivery, "current").result_sent
        async def online(*args, **kwargs):
            return {"ok": True}
        monkeypatch.setattr(center, "call", online)
        await center.flush_outbox(product)
        with product.transaction() as db:
            assert db.get(center.CenterDelivery, "current").result_sent
    asyncio.run(exercise())


def test_center_redelivery_retries_failed_attempt_after_result_ack(product):
    item = delivery("retry")
    first = center.accept(product, item)["request_id"]
    product.finish_request(first, "failed", error="temporary failure")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as blocked:
        center.accept(product, item)
    assert blocked.value.status_code == 409
    center.sent(product, "retry", "failed")
    second = center.accept(product, item)["request_id"]
    assert second != first
    assert center.accept(product, item)["request_id"] == second
    with product.transaction() as db:
        assert db.get(ReplyRequest, second).source == "message_center"
        assert db.get(center.CenterDelivery, "retry").state == "received"


def test_later_private_continuation_updates_notified_and_obsolete_blocks(product):
    first = center.accept(product, delivery("continuation"))["request_id"]
    product.finish_request(first, "completed")
    center.sent(product, "continuation", "processed")
    with product.transaction() as db:
        original = db.get(ReplyRequest, first)
        chat = db.get(Chat, original.chat_id)
        later = product.accept_request(db, chat, original.target_twin_id, original.requester_id,
            "subagent_result", "later-notice", input_body="A worker returned", continuation_of_request_id=first,
            causal_chain_id=original.causal_chain_id)
        later_id = later.id
        product.append_message(db, chat, author_id=original.target_twin_id, kind="twin",
            body="Later useful result", request_id=later.id)
    product.finish_request(later_id, "completed")
    with product.transaction() as db:
        item = db.get(center.CenterDelivery, "continuation")
        assert item.notified and not item.result_sent
    assert not center.refresh_entries(product, later_id, {"continuation": {"status": "withdrawn", "version": 1}})
    with product.transaction() as db:
        assert db.get(center.CenterDelivery, "continuation").state == "obsolete"


def test_agent_search_and_detail_keep_results_bounded(product, monkeypatch):
    request_id = center.accept(product, delivery("lookup"))["request_id"]
    context = product.get_runtime_context(request_id)
    calls = []
    async def fake_call(method, path, *, params=None, body=None):
        calls.append((path, params))
        if path == "/messages":
            return [{"id": "one", "title": "Notice", "body_excerpt": "short", "metadata": {"huge": "x" * 9000}}]
        return {"id": "one", "title": "Notice", "body": "x" * 20000,
            "revisions": [{"id": str(n), "version": n, "content": "y" * 10000} for n in range(40)],
            "responses": [{"owner_id": "owner-maya", "response": "attending", "note": "z" * 600}]}
    monkeypatch.setattr(center, "call", fake_call)
    async def exercise():
        listing = await center.tool(product, "list_center_messages", {"q": "Notice"}, context)
        assert calls[-1][1]["summary"] and calls[-1][1]["limit"] == 20
        assert "metadata" not in listing[0]
        detail = await center.tool(product, "get_center_message", {"id": "one"}, context)
        assert len(detail["body"]) == 16000 and detail["body_truncated"]
        assert "revisions" not in detail
        assert len(detail["responses"][0]["note"]) == 500 and detail["responses"][0]["note_truncated"]
    asyncio.run(exercise())


def test_digest_snapshot_is_small_and_bounded(product):
    for index in range(22):
        item = delivery(str(index), "digest")
        item["message"]["body"] = "x" * 100000
        center.accept(product, item)
    with product.transaction() as db:
        result = center.pending_digest(db, "owner-maya")
    assert len(result) == 20
    assert len(result[0]["body_excerpt"]) == 500 and result[0]["body_truncated"]
    assert "body" not in result[0]["message"]


def test_explicit_consumed_mark_updates_local_inbox(product, monkeypatch):
    item = delivery("marked")
    request_id = center.accept(product, item)["request_id"]
    context = product.get_runtime_context(request_id)
    sent = []
    async def fake_call(method, path, *, params=None, body=None):
        sent.append((path, body))
        return {"status": "processed"}
    monkeypatch.setattr(center, "call", fake_call)
    asyncio.run(center.tool(product, "mark_center_delivery", {"id": "marked"}, context))
    assert sent == [("/deliveries/marked/mark", {"owner_id": "owner-maya", "consumed": True})]
    with product.transaction() as db:
        stored = db.get(center.CenterDelivery, "marked")
        assert stored.state == "processed" and stored.result_sent
    assert not center.refresh_entries(product, request_id, {"marked": item["message"]})


def test_callback_authentication_and_input_boundary(product, monkeypatch):
    import httpx

    from app import service as service_module
    from app.main import app
    monkeypatch.setattr(service_module, "service", product)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://duet") as client:
            value = delivery("boundary", "digest")
            assert (await client.post("/api/message-center/deliveries", json=value)).status_code == 401
            headers = {"X-Message-Center-Token": "test"}
            broken = {**value, "message": {**value["message"], "version": 2}}
            assert (await client.post("/api/message-center/deliveries", headers=headers, json=broken)).status_code == 422
            accepted = await client.post("/api/message-center/deliveries", headers=headers, json=value)
            assert accepted.status_code == 200 and accepted.json() == {"status": "accepted", "request_id": None}
    asyncio.run(exercise())



def test_agent_can_read_complete_long_message_in_slices(product, monkeypatch):
    request_id = center.accept(product, delivery("long"))["request_id"]
    context = product.get_runtime_context(request_id)
    body = ("段落 αβγ\n" + "0123456789abcdef") * 4000
    async def fake_call(method, path, *, params=None, body=None):
        return {"id": "long-message", "version": 4, "title": "Long", "body": original}
    original = body
    monkeypatch.setattr(center, "call", fake_call)

    async def exercise():
        chunks, offset = [], 0
        while offset is not None:
            page = await center.tool(product, "get_center_message", {"id": "long-message", "body_offset": offset, "body_limit": 7000}, context)
            assert page["version"] == 4 and page["body_offset"] == offset
            assert page["body_total_length"] == len(original) and len(page["body"]) <= 7000
            chunks.append(page["body"])
            offset = page["body_next_offset"]
        assert "".join(chunks) == original

    asyncio.run(exercise())
