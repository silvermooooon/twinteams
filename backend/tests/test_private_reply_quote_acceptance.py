"""Independent quote presentation checks; real PG/SDK with an offline provider."""

import asyncio
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from fastapi import HTTPException
from sqlalchemy import select
from test_memory_dream import product as product  # noqa: PLC0414

from app.models import Chat, ContextManifest, Message, ReplyRequest
from app.runtime_bridge import RuntimeBridge


def accepted(product, kind):
    chat_id = "private-alex" if kind == "private" else "group-alex-maya"
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
                author_id="owner-maya",
                kind="human",
                body="Synthetic original question",
            ).id
        request = product.manual_request(
            "owner-maya", chat_id, message_id, "twin-alex", uuid4().hex
        )
    return request, product.get_runtime_context(request["id"]), message_id, chat_id


def message_json(product, chat_id, message_id):
    return next(
        row
        for row in product.chat_detail("owner-alex", chat_id)["messages"]
        if row["id"] == message_id
    )


def assert_lineage(product, request, question_id, output_id):
    with product.transaction() as db:
        output, original = db.get(Message, output_id), db.get(ReplyRequest, request["id"])
        assert output.request_id == original.id and original.source_message_id == question_id
        assert (
            output.causal_chain_id
            == original.causal_chain_id
            == db.get(Message, question_id).causal_chain_id
        )
        assert question_id in output.source_message_ids
        manifests = list(
            db.scalars(select(ContextManifest).where(ContextManifest.request_id == original.id))
        )
        assert manifests and any(
            source.get("id") == question_id for row in manifests for source in row.sources
        )
        assert output.source_provenance == product.manifest_provenance(db, original.id)


@pytest.mark.parametrize("kind", ["private", "group"])
def test_automatic_output_only_suppresses_private_display_quote_and_preserves_audit(product, kind):
    request, _context, question_id, chat_id = accepted(product, kind)
    output = product.publish_runtime_output(
        request["id"],
        "Answer without redundant private quote",
        "quote-session",
        "quote-run",
        "answer-event",
        1,
    )
    visible = message_json(product, chat_id, output)
    if kind == "private":
        assert "reply_to" not in visible
    else:
        assert visible["reply_to"]["id"] == question_id
    assert_lineage(product, request, question_id, output)
    assert (
        product.publish_runtime_output(
            request["id"],
            "Answer without redundant private quote",
            "quote-session",
            "quote-run",
            "answer-event",
            1,
        )
        == output
    )


def test_explicit_private_tool_quote_remains_available_and_source_withdrawal_still_gates_output(
    product,
):
    request, context, question_id, chat_id = accepted(product, "private")
    result = asyncio.run(
        product.runtime_tool(
            "send_chat_message",
            {
                "chat_id": chat_id,
                "body": "This specific quoted reply is intentional",
                "source_message_ids": [question_id],
                "reply_to_message_id": question_id,
            },
            {**context, "operation_id": "explicit-private-quote"},
        )
    )
    assert result["sent"]
    assert message_json(product, chat_id, result["message_id"])["reply_to"]["id"] == question_id
    assert_lineage(product, request, question_id, result["message_id"])
    automatic = product.publish_runtime_output(
        request["id"],
        "Ordinary unquoted response",
        "quote-session",
        "quote-run",
        "ordinary-event",
        2,
    )
    assert "reply_to" not in message_json(product, chat_id, automatic)
    with product.transaction() as db:
        db.get(Message, question_id).state = "withdrawn"
    with pytest.raises(HTTPException) as error:
        product.publish_runtime_output(
            request["id"],
            "Late response must be withheld",
            "quote-session",
            "quote-run",
            "late-event",
            3,
        )
    assert error.value.status_code == 409
    assert all(
        row["id"] not in {automatic, result["message_id"]}
        for row in product.chat_detail("owner-alex", chat_id)["messages"]
    )


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
