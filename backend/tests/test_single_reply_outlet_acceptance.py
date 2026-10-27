"""Independent current-chat reply outlet regression; disposable PG and offline SDK only."""
import asyncio
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from duet_test_support import seed_collaborations, send_message
from fastapi import HTTPException
from sqlalchemy import select
from test_memory_dream import product as product  # noqa: PLC0414

from app.models import Chat, Message, ProductOperation
from app.runtime_bridge import RuntimeBridge
from app.service import digest


def origin(product, chat_id="private-alex", *, reply_for_me=False):
    sender = "owner-maya" if chat_id != "private-alex" and not reply_for_me else "owner-alex"
    if chat_id.startswith("duet-"):
        seed_collaborations(product)
    sent = send_message(product, sender, chat_id, "Synthetic owner task", uuid4().hex)
    request = sent["request"]
    if not request:
        # The observed bug used an accepted automatic task owned by the recipient's Twin.
        with product.transaction() as db:
            row = product.accept_request(db, db.get(Chat, chat_id), "twin-alex", sender,
                "auto", uuid4().hex, source_message_id=sent["message"]["id"])
            request = product.request_json(db, row, sender)
    return request, product.get_runtime_context(request["id"])


def call(product, context, args, operation_id=None):
    return asyncio.run(product.runtime_tool("send_chat_message", args,
        {**context, "operation_id": operation_id or uuid4().hex}))


def outputs(product, request_id, chat_id):
    with product.transaction() as db:
        return [{"id": row.id, "body": row.body, "kind": row.kind, "key": row.sdk_output_key,
            "raw": row.raw_owner_input} for row in db.scalars(select(Message).where(
                Message.request_id == request_id, Message.chat_id == chat_id, Message.state == "published"))]


def complete(product, request_id, body):
    product.complete_request(request_id, {"body": body, "session_id": "synthetic-" + request_id,
        "run_id": "synthetic-run-" + request_id, "event_id": "synthetic-final-" + request_id, "event_seq": 2})


@pytest.mark.parametrize("chat_id", ["private-alex", "duet-alex-maya", "duet-team"])
def test_current_chat_tool_is_rejected_without_mutation_but_normal_reply_is_published_once(product, chat_id):
    request, context = origin(product, chat_id)
    body, operation = "Synthetic current-chat answer", uuid4().hex
    with pytest.raises(HTTPException) as rejected:
        call(product, context, {"chat_id": chat_id, "body": body, "source_message_ids": []}, operation)
    assert rejected.value.status_code in {400, 403}
    assert outputs(product, request["id"], chat_id) == []
    with product.transaction() as db:
        assert db.get(ProductOperation, operation) is None
    complete(product, request["id"], body)
    complete(product, request["id"], body)
    saved = outputs(product, request["id"], chat_id)
    assert len(saved) == 1 and saved[0]["body"] == body and not saved[0]["key"].startswith("tool:")


def test_cross_chat_report_replay_and_equal_text_normal_replies_remain_independent(product):
    request, context = origin(product, "duet-alex-maya")
    body = "Synthetic report text shared with the owner"
    args = {"chat_id": "private-alex", "body": body, "source_message_ids": []}
    operation = uuid4().hex
    sent = call(product, context, args, operation)
    assert sent["sent"] and sent["author_id"] == "twin-alex"
    assert call(product, context, args, operation)["message_id"] == sent["message_id"]
    assert len(outputs(product, request["id"], "private-alex")) == 1
    complete(product, request["id"], body)
    assert len(outputs(product, request["id"], "duet-alex-maya")) == 1
    second, _ = origin(product, "duet-alex-maya")
    complete(product, second["id"], body)
    assert len(outputs(product, second["id"], "duet-alex-maya")) == 1


def test_reply_for_me_cannot_add_an_agent_outlet_in_a_human_chat(product):
    with product.transaction() as db:
        before = db.get(Chat, "group-alex-maya").next_message_seq
    with pytest.raises(HTTPException) as denied:
        product.send_message("owner-alex", "group-alex-maya", "Synthetic rewrite", uuid4().hex, reply_for_me=True)
    assert denied.value.status_code == 403
    with product.transaction() as db:
        assert db.get(Chat, "group-alex-maya").next_message_seq == before


def test_preexisting_committed_same_chat_receipt_replays_without_a_new_effect_and_rechecks_visibility(product):
    request, context = origin(product)
    args = {"chat_id": "private-alex", "body": "Synthetic legacy committed tool reply", "source_message_ids": []}
    operation = uuid4().hex
    with product.transaction() as db:
        chat, member = product.membership(db, "owner-alex", "private-alex")
        legacy = product.append_message(db, chat, author_id="twin-alex", kind="twin", body=args["body"],
            request_id=request["id"], causal_chain_id=request["causal_chain_id"], sdk_output_key="tool:" + digest(operation))
        result = {"status": "sent", "sent": True, "message_id": legacy.id, "chat_id": chat.id,
            "author_id": "twin-alex", "author_owner_id": "owner-alex", "causal_chain_id": request["causal_chain_id"],
            "message": product.message_json(db, legacy, member), "followup": None}
        db.add(ProductOperation(operation_id=operation, chat_id=chat.id, owner_id="owner-alex",
            name="send_chat_message", arguments_hash=digest(args), result=result))
    assert call(product, context, args, operation)["message_id"] == result["message_id"]
    assert len(outputs(product, request["id"], "private-alex")) == 1
    with pytest.raises(HTTPException):
        call(product, context, args, uuid4().hex)
    with product.transaction() as db:
        db.get(Message, result["message_id"]).state = "withdrawn"
    with pytest.raises(HTTPException):
        call(product, context, args, operation)
    assert outputs(product, request["id"], "private-alex") == []


def test_offline_sdk_recovers_from_rejected_current_chat_tool_and_emits_one_normal_reply(product):
    request, context = origin(product)
    body = "Synthetic final answer after a tool rejection"
    calls = []

    async def run():
        runtime = RuntimeBridge(product)

        def offline(model, model_context, options):
            calls.append(model_context)
            stream = AssistantMessageEventStream()
            if len(calls) == 1:
                content, stop = [{"type": "toolCall", "id": "current-chat-send", "name": "send_chat_message",
                    "arguments": {"chat_id": "private-alex", "body": body, "source_message_ids": []}}], "toolUse"
            else:
                assert len(calls) == 2
                results = [row for row in model_context.messages if row.get("role") == "toolResult"]
                assert results[-1]["isError"] is True
                content, stop = [{"type": "text", "text": body}], "stop"
            stream.push({"type": "done", "message": assistant_message(model, content=content, stopReason=stop)})
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        try:
            result = await runtime.execute(product.get_request(request["id"]), context)
            product.complete_request(request["id"], result)
            saved = outputs(product, request["id"], "private-alex")
            assert len(calls) == 2 and len(saved) == 1 and saved[0]["body"] == body
            assert not saved[0]["key"].startswith("tool:")
        finally:
            await runtime.close()

    asyncio.run(run())
