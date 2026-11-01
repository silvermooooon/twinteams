"""Contact Dream uses real PostgreSQL, SDK tools and Git with an offline provider."""
import asyncio
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from fastapi import HTTPException
from sqlalchemy import delete
from test_memory_dream import due
from test_memory_dream import product as product  # noqa: PLC0414

from app import contacts
from app import service as service_module
from app.contacts_dream import seed_contact_dreams
from app.models import Chat, Contact, ContactDreamCursor, MemoryDreamState, Message
from app.runtime_bridge import RuntimeBridge


def publish(product, body="I own Atlas settlement development", *, author="owner-maya", chat_id="group-alex-maya", reply=None):
    with product.transaction() as db:
        return product.append_message(db, db.get(Chat, chat_id), author_id=author, kind="human",
            body=body, reply_to_message_id=reply).id


async def execute(product, request_id, source_id, *, publish_memory=True, private_memory=False):
    runtime, calls = RuntimeBridge(product), []
    def offline(model, context, options):
        calls.append(context)
        results = [row for row in context.messages if row.get("role") == "toolResult"]
        if not results and private_memory:
            call = {"type": "toolCall", "id": "write", "name": "memory_write", "arguments": {
                "key": "MEMORY", "content": "Owner prefers tea.", "expected_version": None}}
        elif len(results) == (1 if private_memory else 0):
            call = {"type": "toolCall", "id": "tags", "name": "stage_contact_tags", "arguments": {
                "owner_id": "owner-maya", "tags": ["Atlas · settlement development"], "source_message_ids": [source_id]}}
        elif publish_memory:
            call = {"type": "toolCall", "id": "publish", "name": "memory_publish", "arguments": {}}
        else:
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=[], stopReason="stop")})
            return stream
        stream = AssistantMessageEventStream()
        stream.push({"type": "done", "message": assistant_message(model, content=[call], stopReason="toolUse")})
        return stream
    runtime.models.register_api("offline-regression", offline)
    await runtime.start()
    try:
        result = await runtime.execute(product.get_request(request_id), product.get_runtime_context(request_id))
        return result, calls
    finally:
        await runtime.close()


def test_automatic_contact_only_dream_stages_until_git_publish_and_then_becomes_idle(product):
    source_id = publish(product)
    request_id = due(product)
    payload = product.get_runtime_context(request_id)["memory_dream"]
    assert payload["messages"] == [] and payload["contact_dream"]["contacts"][0]["owner_id"] == "owner-maya"
    assert contacts.list_contacts(product, "owner-alex")["items"][0]["tags"] == []
    result, calls = asyncio.run(execute(product, request_id, source_id))
    assert len(calls) == 2 and result["body"] == ""
    declarations = {tool["name"] for row in calls[0].messages for tool in row.get("toolsAdded", [])}
    assert "stage_contact_tags" in declarations and "memory_write" not in declarations and "memory_delete" not in declarations
    assert "ONLY for contact tags; never save this in private Memory" in str(calls[0].messages)
    assert contacts.list_contacts(product, "owner-alex")["items"][0]["tags"] == []
    product.complete_request(request_id, result)
    assert contacts.list_contacts(product, "owner-alex")["items"][0]["tags"] == ["Atlas · settlement development"]
    with product.transaction() as db:
        assert db.get(ContactDreamCursor, ("owner-alex", "group-alex-maya")).message_cursor_seq == 1
        state = db.get(MemoryDreamState, "owner-alex")
        assert state.message_cursor_seq == 0 and state.due_at is None and state.last_status == "published"
    product.process_memory_dreams()
    assert len(calls) == 2


def test_failed_dream_keeps_contact_cursor_and_does_not_apply_staged_tags(product):
    source_id = publish(product)
    request_id = due(product)
    with pytest.raises(RuntimeError, match="not published"):
        asyncio.run(execute(product, request_id, source_id, publish_memory=False))
    product.fail_request(request_id, "not published")
    with product.transaction() as db:
        assert db.get(ContactDreamCursor, ("owner-alex", "group-alex-maya")).message_cursor_seq == 0
        assert db.get(Contact, ("owner-alex", "owner-maya")).tags == []
    next_id = due(product)
    assert product.get_runtime_context(next_id)["memory_dream"]["contact_dream"]["messages"][0]["id"] == source_id


def test_manual_clear_blocks_old_evidence_but_later_explicit_evidence_still_updates(product):
    from app import personal
    publish(product, "I prefer tea", author="owner-alex", chat_id="private-alex")
    older_source = publish(product)
    stale_id = due(product)
    contacts.edit_tags(product, "owner-alex", "owner-maya", [])
    product.get_runtime_context(stale_id)
    result, _ = asyncio.run(execute(product, stale_id, older_source, private_memory=True))
    product.complete_request(stale_id, result)
    assert contacts.get_contact(product, "owner-alex", "owner-maya")["contact"]["tags"] == []
    assert asyncio.run(personal.own_memory_store("owner-alex").read("MEMORY")).content == "Owner prefers tea."
    with product.transaction() as db:
        assert db.get(MemoryDreamState, "owner-alex").last_status == "published"
    source_id = publish(product, "I now own Atlas settlement development")
    queued = asyncio.run(product.request_memory_dream("owner-alex"))
    context = product.get_runtime_context(queued["request_id"])
    assert [ref["id"] for ref in context["memory_dream"]["contact_dream"]["messages"]] == [source_id]
    result, _ = asyncio.run(execute(product, queued["request_id"], source_id))
    product.complete_request(queued["request_id"], result)
    assert contacts.list_contacts(product, "owner-alex")["items"][0]["tags"] == ["Atlas · settlement development"]


def test_owner_correction_through_agent_tool_excludes_older_evidence(product):
    publish(product)
    request = product.send_message("owner-alex", "private-alex", "Clear Maya's tags", uuid4().hex)["request"]
    context = product.get_runtime_context(request["id"])
    contact = contacts.list_contacts(product, "owner-alex")["items"][0]
    result = asyncio.run(product.runtime_tool("update_contact_tags", {
        "contact_owner_id": "owner-maya", "tags": [], "expected_revision": contact["revision"],
    }, {**context, "operation_id": uuid4().hex}))
    assert result["manual_updated_at"] is not None
    from app.contacts_dream import batch
    with product.transaction() as db:
        payload, evidence = batch(product, db, "owner-alex")
        assert payload["contacts"] == [] and evidence == []
    assert contacts.list_contacts(product, "owner-alex")["items"][0]["tags"] == []


def test_owner_edit_after_git_publication_skips_stale_tags_and_completes_dream(product):
    source_id = publish(product)
    request_id = due(product)
    result, _ = asyncio.run(execute(product, request_id, source_id))
    contacts.edit_tags(product, "owner-alex", "owner-maya", ["Owner correction"])
    product.complete_request(request_id, result)
    with product.transaction() as db:
        assert db.get(Contact, ("owner-alex", "owner-maya")).tags == ["Owner correction"]
        assert db.get(MemoryDreamState, "owner-alex").last_status == "published"
        assert db.get(ContactDreamCursor, ("owner-alex", "group-alex-maya")).message_cursor_seq == 1


def test_provisioning_enrolls_existing_history_once_without_new_private_messages(product):
    with product.transaction() as db:
        chat = db.get(Chat, "group-alex-maya")
        source = Message(id=uuid4().hex, chat_id=chat.id, seq=chat.next_message_seq,
            author_id="owner-maya", kind="human", state="published", body="I own Atlas settlement development")
        db.add(source)
        chat.next_message_seq += 1
        source_id = source.id
        db.execute(delete(ContactDreamCursor))
        db.execute(delete(MemoryDreamState))
    with service_module.engine.begin() as connection:
        seed_contact_dreams(connection)
    request_id = due(product)
    with product.transaction() as db:
        request_id = db.get(MemoryDreamState, "owner-alex").queued_request_id
        assert request_id
    result, _ = asyncio.run(execute(product, request_id, source_id))
    product.complete_request(request_id, result)
    with service_module.engine.begin() as connection:
        seed_contact_dreams(connection)
    with product.transaction() as db:
        assert db.get(MemoryDreamState, "owner-alex").due_at is None
        assert db.get(ContactDreamCursor, ("owner-alex", "group-alex-maya")).message_cursor_seq == 1


def test_contact_evidence_is_bounded_and_does_not_enroll_unrelated_group_members(product):
    general = publish(product, chat_id="group-team")
    with product.transaction() as db:
        assert db.get(ContactDreamCursor, ("owner-alex", "group-team")) is None
    publish(product, author="owner-alex", chat_id="group-team", reply=general)
    with product.transaction() as db:
        assert db.get(ContactDreamCursor, ("owner-alex", "group-team"))
        assert db.get(ContactDreamCursor, ("owner-jordan", "group-team")) is None
    for _ in range(25):
        publish(product)
    request_id = due(product)
    payload = product.get_runtime_context(request_id)["memory_dream"]["contact_dream"]
    assert len(payload["messages"]) <= 20
    assert sum(page["through_seq"] - page["cursor_before"] for page in payload["cursors"]) <= 100


def test_contact_evidence_is_rechecked_if_owner_loses_history_visibility(product):
    from sqlalchemy import select

    from app.models import Membership
    publish(product)
    request_id = due(product)
    with product.transaction() as db:
        member = db.scalar(select(Membership).where(Membership.owner_id == "owner-alex",
            Membership.chat_id == "group-alex-maya"))
        member.lower_seq = 2
    with pytest.raises(HTTPException):
        product.get_runtime_context(request_id)


def test_explicit_mention_schedules_both_participants_but_not_bystanders(product):
    publish(product, "@BBB I own Atlas settlement development", chat_id="group-team", author="owner-alex")
    with product.transaction() as db:
        assert db.get(ContactDreamCursor, ("owner-alex", "group-team"))
        assert db.get(ContactDreamCursor, ("owner-maya", "group-team"))
        assert db.get(MemoryDreamState, "owner-maya").due_at
        assert db.get(ContactDreamCursor, ("owner-jordan", "group-team")) is None


def test_large_private_input_does_not_block_bounded_contact_dream(product):
    publish(product, "私密内容" * 16000, author="owner-alex", chat_id="private-alex")
    source_id = publish(product)
    request_id = due(product)
    payload = product.get_runtime_context(request_id)["memory_dream"]
    assert payload["messages"] == []
    result, _ = asyncio.run(execute(product, request_id, source_id))
    product.complete_request(request_id, result)
    with product.transaction() as db:
        assert db.get(MemoryDreamState, "owner-alex").message_cursor_seq == 0
        assert db.get(MemoryDreamState, "owner-alex").due_at is not None


@pytest.mark.parametrize("lost_ack", [False, True])
def test_mixed_dream_keeps_human_contact_evidence_out_of_memory_provenance(product, monkeypatch, lost_ack):
    if lost_ack:
        from agent_runtime.memory.git import GitMemoryDraft, MemoryPublishUnknown
        original = GitMemoryDraft.publish
        async def publish_without_ack(self, operation_id=None):
            await original(self, operation_id)
            raise MemoryPublishUnknown("Synthetic lost acknowledgement")
        monkeypatch.setattr(GitMemoryDraft, "publish", publish_without_ack)
    from app import personal
    private_id = publish(product, "I prefer tea.", author="owner-alex", chat_id="private-alex")
    human_id = publish(product)
    request_id = due(product)
    result, calls = asyncio.run(execute(product, request_id, human_id, private_memory=True))
    product.complete_request(request_id, result)
    entry = asyncio.run(personal.own_memory_store("owner-alex").read("MEMORY"))
    assert entry.content == "Owner prefers tea."
    assert {source.event_id for source in entry.sources} == {private_id}
    assert human_id not in {source.event_id for source in entry.sources}
    assert "NEVER write it" in str(calls[0].messages)


def test_queued_human_message_keeps_next_dream_when_previous_finishes_before_commit(product, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app import memory_dream
    from app.contacts_dream import pending
    started = Event()
    original_finish = memory_dream.finish
    def finish(*args, **kwargs):
        started.set()
        return original_finish(*args, **kwargs)
    monkeypatch.setattr(memory_dream, "finish", finish)
    publish(product)
    request_id = due(product)
    payload = product.get_runtime_context(request_id)["memory_dream"]
    result = {"memory_dream": {"published": True, "version": "verified-commit",
        "cursor_before": payload["cursor_before"], "through_seq": payload["through_seq"],
        "contact_proposals": []}}
    with ThreadPoolExecutor(max_workers=1) as pool:
        with product.transaction() as db:
            product.append_message(db, db.get(Chat, "group-alex-maya"), author_id="owner-maya",
                kind="human", body="I now own Atlas billing development")
            # Completion holds its private chat lock while this human message remains uncommitted.
            completion = pool.submit(product.complete_request, request_id, result)
            assert started.wait(timeout=10)
        completion.result(timeout=10)
        with product.transaction() as db:
            state = db.get(MemoryDreamState, "owner-alex")
            assert state.queued_request_id is None and state.due_at is not None
            assert pending(db, "owner-alex")
    next_id = due(product)
    assert product.get_runtime_context(next_id)["memory_dream"]["contact_dream"]["messages"][0]["id"] != payload["contact_dream"]["messages"][0]["id"]


def test_finishing_stale_loaded_state_preserves_concurrent_due_hint(product):
    from app import memory_dream
    from app.models import ReplyRequest
    publish(product)
    request_id = due(product)
    payload = product.get_runtime_context(request_id)["memory_dream"]
    with service_module.SessionLocal.begin() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        request = db.get(ReplyRequest, request_id)
        assert state.due_at is None
        publish(product, "I now own Atlas billing development")
        with product.transaction() as other:
            deadline = other.get(MemoryDreamState, "owner-alex").due_at
        memory_dream.finish(product, db, request, "completed", {"published": True,
            "version": "verified-commit", "cursor_before": payload["cursor_before"],
            "through_seq": payload["through_seq"], "contact_proposals": []})
    with product.transaction() as db:
        assert db.get(MemoryDreamState, "owner-alex").due_at == deadline


@pytest.mark.parametrize("known_proposals", [False, True])
def test_manual_publication_confirmation_preserves_unrecovered_contact_evidence(product, known_proposals):
    publish(product, "I prefer tea", author="owner-alex", chat_id="private-alex")
    publish(product)
    request_id = due(product)
    product.mark_memory_dream_recovery(request_id, "Unknown Git acknowledgement")
    product.fail_request(request_id, "Unknown Git acknowledgement")
    if known_proposals:
        product.confirm_memory_dream_publication(request_id, "verified-commit", [])
    else:
        product.confirm_memory_dream_publication(request_id, "verified-commit")
    with product.transaction() as db:
        assert db.get(MemoryDreamState, "owner-alex").message_cursor_seq == 1
        assert db.get(MemoryDreamState, "owner-alex").last_status == "published"
        assert db.get(ContactDreamCursor, ("owner-alex", "group-alex-maya")).message_cursor_seq == (1 if known_proposals else 0)
        if not known_proposals:
            assert db.get(MemoryDreamState, "owner-alex").due_at is not None
    if not known_proposals:
        next_id = due(product)
        assert product.get_runtime_context(next_id)["memory_dream"]["contact_dream"]["messages"]


def test_losing_unrelated_chat_access_does_not_drop_authorized_contact_proposal(product):
    from sqlalchemy import select

    from app.models import Membership, now
    other_chat = product.create_chat("owner-alex", ["owner-jordan"])["id"]
    source_id = publish(product)
    publish(product, author="owner-jordan", chat_id=other_chat)
    request_id = due(product)
    payload = product.get_runtime_context(request_id)["memory_dream"]["contact_dream"]
    assert len(payload["cursors"]) == 2
    result, _ = asyncio.run(execute(product, request_id, source_id))
    with product.transaction() as db:
        member = db.scalar(select(Membership).where(Membership.owner_id == "owner-alex",
            Membership.chat_id == other_chat))
        member.left_at = now()
    product.complete_request(request_id, result)
    assert contacts.get_contact(product, "owner-alex", "owner-maya")["contact"]["tags"] == ["Atlas · settlement development"]


def test_queued_due_hint_skips_only_its_private_revision_notification(product):
    publish(product)
    request_id = due(product)
    with product.transaction() as db:
        before = db.get(Chat, "private-alex").revision
    publish(product, "I now own Atlas billing development")
    with product.transaction() as db:
        assert db.get(Chat, "private-alex").revision == before
        assert db.get(MemoryDreamState, "owner-alex").due_at is not None
    product.fail_request(request_id, "Synthetic failure")
    with product.transaction() as db:
        assert db.get(Chat, "private-alex").revision > before


def test_ordinary_group_message_dream_hook_performs_no_database_queries(product):
    from sqlalchemy import event

    from app.contacts_dream import message_published
    source_id = publish(product, "General announcement", chat_id="group-team")
    statements = []
    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    with product.transaction() as db:
        chat, source = db.get(Chat, "group-team"), db.get(Message, source_id)
        event.listen(service_module.engine, "before_cursor_execute", record)
        try:
            message_published(product, db, chat, source)
        finally:
            event.remove(service_module.engine, "before_cursor_execute", record)
    assert statements == []
