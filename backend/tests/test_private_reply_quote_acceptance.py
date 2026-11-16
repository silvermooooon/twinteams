"""Independent quote presentation checks; real PG/SDK with an offline provider."""

import asyncio
import json
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from duet_test_support import request_duet_reply as receiver_request
from fastapi import HTTPException
from sqlalchemy import select
from test_memory_dream import product as product  # noqa: PLC0414

from app.models import Chat, ContextManifest, Message, ReplyRequest
from app.runtime_bridge import RuntimeBridge


def accepted(product, kind):
    chat_id = "private-alex" if kind == "private" else "duet-alex-maya"
    if kind == "private":
        sent = product.send_message(
            "owner-alex", chat_id, "Synthetic original question", uuid4().hex
        )
        request, message_id = sent["request"], sent["message"]["id"]
    else:
        with product.transaction() as db:
            message_id = product.append_message(
                db,
                db.get(Chat, chat_id),
                author_id="twin-maya",
                kind="twin",
                body="Synthetic original question",
            ).id
        request = receiver_request(product,
            "owner-maya", chat_id, message_id, "twin-alex", uuid4().hex
        )
    context = product.get_runtime_context(request["id"])
    if kind != "private":
        assert context["messages"] == [] and context["source_message"] is None
        read = asyncio.run(product.runtime_tool("read_duet_topic", {"chat_id": chat_id},
            {**context, "operation_id": uuid4().hex}))
        assert any(row["id"] == message_id for row in read["messages"])
    return request, context, message_id, chat_id


def message_json(product, chat_id, message_id):
    return next(
        row
        for row in product.chat_detail("owner-alex", chat_id)["messages"]
        if row["id"] == message_id
    )


def assert_lineage(product, request, question_id, output_id):
    with product.transaction() as db:
        output, original = db.get(Message, output_id), db.get(ReplyRequest, request["id"])
        assert output.request_id == original.id and output.causal_chain_id == original.causal_chain_id
        prepared = db.get(Chat, output.chat_id).kind == "duet"
        if prepared:
            assert original.source_message_id is None
            assert json.loads(original.input_body)["update_id"] == question_id
            assert output.source_message_ids == [] and output.source_provenance == []
        else:
            assert original.source_message_id == question_id
            assert original.causal_chain_id == db.get(Message, question_id).causal_chain_id
            assert question_id in output.source_message_ids
        manifests = list(
            db.scalars(select(ContextManifest).where(ContextManifest.request_id == original.id))
        )
        assert manifests and any(
            source.get("id") == question_id for row in manifests for source in row.sources
        )
        if not prepared:
            assert output.source_provenance == product.manifest_provenance(db, original.id)


@pytest.mark.parametrize("kind", ["private", "duet"])
def test_private_final_is_unquoted_and_topic_reply_requires_explicit_quote_tool(product, kind):
    request, context, question_id, chat_id = accepted(product, kind)
    output = product.publish_runtime_output(request["id"], "Answer without redundant private quote",
        request["session_id"], "quote-run", "answer-event", 1)
    if kind == "private":
        assert "reply_to" not in message_json(product, chat_id, output)
        assert product.publish_runtime_output(request["id"], "Answer without redundant private quote",
            request["session_id"], "quote-run", "answer-event", 1) == output
    else:
        assert output is None
        args = {"chat_id": chat_id, "body": "An intentional quoted topic reply",
            "mention_owner_ids": [], "reply_to_message_id": question_id}
        operation = {**context, "operation_id": uuid4().hex}
        posted = asyncio.run(product.runtime_tool("post_duet_reply", args, operation))
        output = posted["message_id"]
        assert asyncio.run(product.runtime_tool("post_duet_reply", args, operation))["message_id"] == output
        assert message_json(product, chat_id, output)["reply_to"]["id"] == question_id
    assert_lineage(product, request, question_id, output)


def test_cross_chat_private_tool_quote_remains_available_and_source_withdrawal_still_gates_output(
    product,
):
    request, context, question_id, chat_id = accepted(product, "group")
    target_id = "private-alex"
    with product.transaction() as db:
        quoted_id = product.append_message(
            db, db.get(Chat, target_id), author_id="owner-alex", kind="human",
            body="Synthetic earlier private question",
        ).id
    result = asyncio.run(
        product.runtime_tool(
            "send_chat_message",
            {
                "chat_id": target_id,
                "body": "This specific quoted reply is intentional",
                "source_message_ids": [question_id],
                "reply_to_message_id": quoted_id,
            },
            {**context, "operation_id": "explicit-private-quote"},
        )
    )
    assert result["sent"]
    assert message_json(product, target_id, result["message_id"])["reply_to"]["id"] == quoted_id
    with product.transaction() as db:
        sent = db.get(Message, result["message_id"])
        assert sent.request_id == request["id"] and sent.causal_chain_id == request["causal_chain_id"]
        assert sent.source_message_ids == [] and sent.source_provenance == []
    automatic = asyncio.run(product.runtime_tool("post_duet_reply", {
        "chat_id": chat_id, "body": "Intentional topic reply", "mention_owner_ids": [],
        "reply_to_message_id": question_id,
    }, {**context, "operation_id": uuid4().hex}))["message_id"]
    assert message_json(product, chat_id, automatic)["reply_to"]["id"] == question_id
    assert_lineage(product, request, question_id, automatic)
    with product.transaction() as db:
        db.get(Message, question_id).state = "withdrawn"
    with pytest.raises(HTTPException) as error:
        asyncio.run(product.runtime_tool("post_duet_reply", {
            "chat_id": chat_id, "body": "Late response must be withheld", "mention_owner_ids": [],
        }, {**context, "operation_id": uuid4().hex}))
    assert error.value.status_code == 409
    # Withdrawing the original does not retract an already published prepared reply.
    # It removes the quote text and prevents any subsequent publication from that task.
    published = message_json(product, chat_id, automatic)
    assert published["reply_to"]["id"] == question_id and published["reply_to"]["unavailable"]
    assert any(row["id"] == result["message_id"]
        for row in product.chat_detail("owner-alex", target_id)["messages"])


def test_real_sdk_private_answer_is_unquoted_without_losing_request_lineage(product):
    request, _context, question_id, chat_id = accepted(product, "private")

    async def run():
        runtime = RuntimeBridge(product)
        await runtime.start()
        product.bind_runtime(runtime)
        calls = []

        def offline(model, context, options):
            calls.append(context.messages)
            stream = AssistantMessageEventStream()
            stream.push(
                {
                    "type": "done",
                    "message": assistant_message(
                        model,
                        content=[{"type": "text", "text": "A direct private answer."}],
                        stopReason="stop",
                    ),
                }
            )
            return stream

        runtime.models.register_api("offline-regression", offline)
        try:
            await product.execute_request(request["id"])
            assert len(calls) == 1 and product.get_request(request["id"])["status"] == "completed"
            outputs = [
                row
                for row in product.chat_detail("owner-alex", chat_id)["messages"]
                if row["body"] == "A direct private answer."
            ]
            assert len(outputs) == 1 and "reply_to" not in outputs[0]
            assert_lineage(product, request, question_id, outputs[0]["id"])
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.fixture(autouse=True)
def approved_collaboration_fixtures(product):
    from duet_test_support import seed_collaborations
    seed_collaborations(product)
