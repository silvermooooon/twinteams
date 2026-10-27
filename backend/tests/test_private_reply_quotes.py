"""Private auto output keeps audit lineage without rendering a default quote."""
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_memory_dream import product as product  # noqa: PLC0414

from app.models import Message, ReplyRequest


def request_for(product, chat_id):
    from duet_test_support import request_duet_reply, send_message
    sent = send_message(product, "owner-maya" if chat_id.startswith("duet-") else "owner-alex", chat_id, "Original question", uuid4().hex)
    request = sent["request"] or request_duet_reply(product, "owner-maya", chat_id, sent["message"]["id"], "twin-alex", uuid4().hex)
    return sent["message"], request, product.get_runtime_context(request["id"])


@pytest.mark.parametrize("chat_id,quoted", [("private-alex", False), ("duet-alex-maya", True)])
def test_auto_publication_quote_policy_preserves_source_audit_and_replay(product, chat_id, quoted):
    source, request, _context = request_for(product, chat_id)
    output = product.publish_runtime_output(request["id"], "Answer", request["session_id"], "offline-run", "offline-event", 1)
    assert product.publish_runtime_output(request["id"], "Answer", request["session_id"], "offline-run", "offline-event", 1) == output
    with product.transaction() as db:
        row = db.get(Message, output)
        assert row.reply_to_message_id == (source["id"] if quoted else None)
        assert row.request_id == request["id"] and row.causal_chain_id == request["causal_chain_id"]
        assert source["id"] in row.source_message_ids
        assert any(item["kind"] == "message" and item["id"] == source["id"] for item in row.source_provenance)
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
def test_child_continuation_does_not_reintroduce_automatic_private_quote(product, chat_id, quoted):
    source, parent, context = request_for(product, chat_id)
    child = product.accept_child_result({"id": uuid4().hex, "body": "Child completed work"}, context)
    assert child["source"] == "subagent_result"
    with product.transaction() as db:
        request = db.get(ReplyRequest, child["id"])
        assert request.source_message_id == source["id"]
        assert request.reply_to_message_id == (source["id"] if quoted else None)
        assert request.causal_chain_id == parent["causal_chain_id"]
    product.get_runtime_context(child["id"])
    output = product.publish_runtime_output(child["id"], "Child result report", child["session_id"], "child-run", "child-final", 1)
    message = next(row for row in product.chat_detail("owner-alex", chat_id)["messages"] if row["id"] == output)
    assert ("reply_to" in message) is quoted
    with product.transaction() as db:
        assert source["id"] in db.get(Message, output).source_message_ids


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
