"""Private finals omit default quotes; topic quotes require explicit publication."""
import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_memory_dream import product as product  # noqa: PLC0414

from app.models import ContextManifest, Message, ReplyRequest


def request_for(product, chat_id):
    from duet_test_support import request_duet_reply, send_message
    sent = send_message(product, "owner-maya" if chat_id.startswith("duet-") else "owner-alex", chat_id, "Original question", uuid4().hex)
    request = sent["request"] or request_duet_reply(product, "owner-maya", chat_id, sent["message"]["id"], "twin-alex", uuid4().hex)
    context = product.get_runtime_context(request["id"])
    if context["chat"]["kind"] == "duet":
        assert context["messages"] == [] and context["source_message"] is None
        asyncio.run(product.runtime_tool("read_duet_topic", {"chat_id": chat_id}, context))
    return sent["message"], request, context


def publish_answer(product, request, context, source, body, event):
    output = product.publish_runtime_output(request["id"], body, request["session_id"], "offline-run", event, 1)
    assert product.publish_runtime_output(request["id"], body, request["session_id"], "offline-run", event, 1) == output
    if context["chat"]["kind"] == "duet":
        assert output is None
        args = {"chat_id": context["chat"]["id"], "body": body,
                "reply_to_message_id": source["id"], "mention_owner_ids": []}
        posting = {**context, "operation_id": event}
        result = asyncio.run(product.runtime_tool("post_duet_reply", args, posting))
        assert asyncio.run(product.runtime_tool("post_duet_reply", args, posting))["message_id"] == result["message_id"]
        return result["message_id"]
    return output


@pytest.mark.parametrize("chat_id,quoted", [("private-alex", False), ("duet-alex-maya", True)])
def test_publication_quote_policy_preserves_source_audit_and_replay(product, chat_id, quoted):
    source, request, context = request_for(product, chat_id)
    output = publish_answer(product, request, context, source, "Answer", "offline-event")
    with product.transaction() as db:
        row = db.get(Message, output)
        assert row.reply_to_message_id == (source["id"] if quoted else None)
        assert row.request_id == request["id"] and row.causal_chain_id == request["causal_chain_id"]
        manifests = list(db.scalars(select(ContextManifest).where(ContextManifest.request_id == request["id"])))
        assert any(item["kind"] == ("duet_post_revision" if quoted else "message") and item["id"] == source["id"]
            for manifest in manifests for item in manifest.sources)
        assert len(list(db.scalars(select(Message).where(Message.request_id == request["id"])))) == 1
    visible = next(row for row in product.chat_detail("owner-alex", chat_id)["messages"] if row["id"] == output)
    assert ("reply_to" in visible) is quoted


def test_explicit_private_human_quotes_preserve_visible_reference(product):
    source, _, _ = request_for(product, "private-alex")
    human = product.send_message("owner-alex", "private-alex", "Explicit human quote", uuid4().hex,
        reply_to=source["id"])["message"]
    assert human["reply_to"]["id"] == source["id"]
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        product.send_message("owner-alex", "private-alex", "Removed reply-for-me", uuid4().hex,
            reply_to=source["id"], reply_for_me=True)


@pytest.mark.parametrize("chat_id,quoted", [("private-alex", False), ("duet-alex-maya", True)])
def test_child_continuation_keeps_audit_and_requires_explicit_topic_quote(product, chat_id, quoted):
    source, parent, context = request_for(product, chat_id)
    child = product.accept_child_result({"id": uuid4().hex, "body": "Child completed work"}, context)
    assert child["source"] == "subagent_result"
    with product.transaction() as db:
        request = db.get(ReplyRequest, child["id"])
        assert request.source_message_id == (None if quoted else source["id"])
        assert request.reply_to_message_id is None
        assert request.continuation_of_request_id == parent["id"]
        assert request.causal_chain_id == parent["causal_chain_id"]
    child_context = product.get_runtime_context(child["id"])
    if quoted:
        assert child_context["messages"] == [] and child_context["source_message"] is None
    output = publish_answer(product, child, child_context, source, "Child result report", "child-final")
    message = next(row for row in product.chat_detail("owner-alex", chat_id)["messages"] if row["id"] == output)
    assert ("reply_to" in message) is quoted
    with product.transaction() as db:
        manifests = list(db.scalars(select(ContextManifest).where(ContextManifest.request_id == child["id"])))
        assert any(item["kind"] == ("duet_post_revision" if quoted else "message") and item["id"] == source["id"]
            for manifest in manifests for item in manifest.sources)


def test_private_child_of_explicit_human_quote_keeps_lineage_without_default_quote(product):
    source, _, _ = request_for(product, "private-alex")
    parent = product.send_message("owner-alex", "private-alex", "Explicit quoted task", uuid4().hex,
        reply_to=source["id"])["request"]
    context = product.get_runtime_context(parent["id"])
    child = product.accept_child_result({"id": uuid4().hex, "body": "Explicit task completed"}, context)
    with product.transaction() as db:
        row = db.get(ReplyRequest, child["id"])
        assert row.source_message_id == parent["source_message_id"] and row.reply_to_message_id is None
    product.get_runtime_context(child["id"])
    output = product.publish_runtime_output(child["id"], "Explicit child answer", child["session_id"], "explicit-child-run", "explicit-child-event", 1)
    message = next(row for row in product.chat_detail("owner-alex", "private-alex")["messages"] if row["id"] == output)
    assert "reply_to" not in message


@pytest.fixture(autouse=True)
def approved_collaboration_fixtures(product):
    from duet_test_support import seed_collaborations
    seed_collaborations(product)
