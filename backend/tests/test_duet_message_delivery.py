"""Published Duet messages are durable per-recipient input, using isolated PG."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from duet_test_support import append_duet, approved_chat
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_memory_dream import product as product  # noqa: PLC0414

from app import api, duet_conversations
from app.models import Actor, CausalChain, Chat, Membership, Message, ReplyRequest, now


def origin(product, owner="owner-alex"):
    request = product.send_message(owner, owner.replace("owner-", "private-"), "Coordinate the authorized task", uuid4().hex)["request"]
    return request, product.get_runtime_context(request["id"])


def send(product, context, chat_id, body, *, operation=None):
    return product._runtime_tool_transaction("send_chat_message",
        {"chat_id": chat_id, "body": body, "source_message_ids": []},
        {**context, "operation_id": operation or uuid4().hex})


def receipts(db, message_id):
    return list(db.scalars(select(ReplyRequest).where(ReplyRequest.source == "duet_message",
        ReplyRequest.source_message_id == message_id, ReplyRequest.generation == 0)))


def continue_request(product, monkeypatch, chat, chain_id, *, owner="owner-maya", key=None):
    monkeypatch.setattr(api, "service", product)
    app = FastAPI(); app.include_router(api.router)
    app.dependency_overrides[api.identity] = lambda: owner
    with TestClient(app) as client:
        return client.post(f"/api/duet-conversations/{chat}/chains/{chain_id}/continue",
            json={"client_request_key": key or uuid4().hex, "expected_allowance": 6})


@pytest.mark.parametrize("owners", [("owner-alex", "owner-maya"), ("owner-alex", "owner-maya", "owner-jordan")])
@pytest.mark.parametrize("body", ["Please review", "Progress so far", "Thanks, complete."])
def test_publication_delivers_each_other_member_once_without_classifier(product, owners, body):
    chat = approved_chat(product, "duet-delivery", list(owners))
    _, context = origin(product)
    operation = uuid4().hex
    first = send(product, context, chat, body, operation=operation)
    replay = send(product, context, chat, body, operation=operation)
    assert first["message_id"] == replay["message_id"]
    with product.transaction() as db:
        message = db.get(Message, first["message_id"])
        assert message.state == "published" and message.seq is not None
        rows = receipts(db, message.id)
        assert {db.get(Actor, row.target_twin_id).owner_id for row in rows} == set(owners) - {"owner-alex"}
        assert len(rows) == len(owners) - 1
        assert all(row.background and row.requester_id == "owner-alex" for row in rows)
        assert all(row.client_request_key == row.intent_key == f"duet-message:{message.id}:{row.target_twin_id.replace('twin-', 'owner-')}" for row in rows)
        assert db.get(CausalChain, message.causal_chain_id).spent == 1
        product._publish_duet_message(db, db.get(Chat, chat), message)
        product._deliver_duet_message(db, db.get(Chat, chat), message)
        assert len(receipts(db, message.id)) == len(rows)
        assert db.get(CausalChain, message.causal_chain_id).spent == 1


def test_message_and_all_receivers_rollback_in_one_transaction(product):
    chat_id = approved_chat(product, "duet-atomic", ["owner-alex", "owner-maya", "owner-jordan"])
    with product.transaction() as db:
        before = db.scalar(select(func.count()).select_from(ReplyRequest))
    message_id = None
    with pytest.raises(RuntimeError, match="rollback synthetic transaction"), product.transaction() as db:
            chat = db.get(Chat, chat_id)
            chain = product.new_causal_chain(db, "owner-alex")
            message = product.append_message(db, chat, author_id="twin-alex", kind="twin", body="Atomic fixture", causal_chain_id=chain.id)
            message_id = message.id
            assert len(receipts(db, message.id)) == 2
            raise RuntimeError("rollback synthetic transaction")
    with product.transaction() as db:
        assert db.get(Message, message_id) is None
        assert db.scalar(select(func.count()).select_from(ReplyRequest)) == before


def test_concurrent_tool_replay_and_service_restart_do_not_duplicate_receivers(product):
    chat = approved_chat(product, "duet-replay")
    _, context = origin(product)
    operation = uuid4().hex
    with ThreadPoolExecutor(max_workers=4) as pool:
        sent = list(pool.map(lambda _: send(product, context, chat, "One committed send", operation=operation), range(4)))
    assert len({value["message_id"] for value in sent}) == 1
    from app.service import ProductService
    restarted = ProductService()
    try:
        replay = send(restarted, context, chat, "One committed send", operation=operation)
        assert replay["message_id"] == sent[0]["message_id"]
        with restarted.transaction() as db:
            assert len(receipts(db, replay["message_id"])) == 1
    finally:
        restarted.db_pool.shutdown()


def test_prepared_peer_text_preserves_audit_without_granting_private_history(product):
    chat = approved_chat(product, "duet-audit")
    request, context = origin(product)
    result = send(product, context, chat, "Only this prepared text is shared")
    with product.transaction() as db:
        message = db.get(Message, result["message_id"])
        assert message.request_id == request["id"]
        assert message.source_message_ids == [] and message.source_provenance == []
        member = product.membership(db, "owner-maya", chat)[1]
        rendered = product.message_json(db, product.visible_message(db, member, message.id), member)
        assert rendered["request_id"] is None
    with pytest.raises(HTTPException):
        product.chat_detail("owner-maya", "private-alex")


@pytest.mark.parametrize("restriction", ["withdrawn", "history", "left"])
def test_receiver_retry_rechecks_source_and_never_republishes_message(product, restriction):
    chat = approved_chat(product, "duet-retry")
    message = append_duet(product, chat, "owner-alex", "Retry only while this source remains authorized")
    with product.transaction() as db:
        receiver = receipts(db, message["id"])[0]
        receiver_id = receiver.id
    product.get_runtime_context(receiver_id)
    product.fail_request(receiver_id, "Offline synthetic failure")
    with product.transaction() as db:
        row = db.get(Message, message["id"])
        membership = db.scalar(select(Membership).where(Membership.chat_id == chat, Membership.owner_id == "owner-maya"))
        if restriction == "withdrawn":
            row.state = "withdrawn"
        elif restriction == "history":
            membership.lower_seq = row.seq + 1; membership.grant_version += 1
        else:
            membership.left_at = now()
    with pytest.raises(HTTPException):
        product.retry_request("owner-maya", receiver_id, uuid4().hex)


def test_closed_peer_conversation_blocks_receiver_execution_and_late_send(product):
    chat = approved_chat(product, "duet-revoked")
    _, context = origin(product)
    sent = send(product, context, chat, "Committed before close")
    with product.transaction() as db:
        receiver_id = receipts(db, sent["message_id"])[0].id
    product.get_runtime_context(receiver_id)
    duet_conversations.close_conversation(product, "owner-maya", chat)
    with pytest.raises(HTTPException):
        product.get_runtime_context(receiver_id)
    with pytest.raises(HTTPException):
        send(product, context, chat, "Forbidden late send")


def test_sixth_is_received_owner_report_is_free_and_continue_publishes_seventh_once(product, monkeypatch):
    chat = approved_chat(product, "duet-budget")
    request, context = origin(product)
    sends = [send(product, context, chat, f"Peer message {index}") for index in range(6)]
    pending_operation = uuid4().hex
    sends.append(send(product, context, chat, "Peer message 6", operation=pending_operation))
    assert send(product, context, chat, "Peer message 6", operation=pending_operation)["message_id"] == sends[-1]["message_id"]
    with product.transaction() as db:
        chain = db.get(CausalChain, request["causal_chain_id"])
        assert chain.spent == chain.allowance == 6
        sixth, seventh = [db.get(Message, value["message_id"]) for value in sends[-2:]]
        assert sixth.state == "published" and len(receipts(db, sixth.id)) == 1
        assert seventh.state == "pending" and seventh.seq is None and receipts(db, seventh.id) == []
        receiver_id = receipts(db, sixth.id)[0].id
    receiver_context = product.get_runtime_context(receiver_id)
    report = send(product, receiver_context, "private-maya", "Owner may read the sixth message's result")
    assert report["sent"]
    with product.transaction() as db:
        assert db.get(CausalChain, request["causal_chain_id"]).spent == 6
    # A peer owner can approve more sends without receiving the sender's draft.
    detail = duet_conversations.detail(product, "owner-maya", chat)
    assert not detail["pending_messages"]
    monkeypatch.setattr(api, "service", product)
    app = FastAPI(); app.include_router(api.router)
    app.dependency_overrides[api.identity] = lambda: "owner-maya"
    body = {"client_request_key": uuid4().hex, "expected_allowance": 6}
    path = f"/api/duet-conversations/{chat}/chains/{request['causal_chain_id']}/continue"
    with TestClient(app) as client:
        first = client.post(path, json=body)
        assert first.status_code == 200, first.text
        replay = client.post(path, json=body)
        assert replay.status_code == 200
    with product.transaction() as db:
        seventh = db.get(Message, sends[-1]["message_id"])
        assert seventh.state == "published" and seventh.seq is not None
        assert len(receipts(db, seventh.id)) == 1
        chain = db.get(CausalChain, request["causal_chain_id"])
        assert chain.spent == 7 and chain.allowance == 12


@pytest.mark.parametrize("fault", ["second_recipient", "withdrawn_source"])
def test_continue_failure_rolls_back_grant_send_sequence_and_every_receiver(product, monkeypatch, fault):
    chat = approved_chat(product, "duet-continue-atomic", ["owner-alex", "owner-maya", "owner-jordan"])
    request, context = origin(product)
    sends = [send(product, context, chat, f"Authorized peer send {index}") for index in range(7)]
    pending_id = sends[-1]["message_id"]
    with product.transaction() as db:
        next_sequence = db.get(Chat, chat).next_message_seq
        receiver_count = db.scalar(select(func.count()).select_from(ReplyRequest))
        if fault == "withdrawn_source":
            db.get(Message, request["source_message_id"]).state = "withdrawn"
    if fault == "second_recipient":
        original = product.accept_request
        accepted = []

        def fail_second(db, channel, twin_id, *args, **kwargs):
            if kwargs.get("source_message_id") == pending_id:
                accepted.append(twin_id)
                if len(accepted) == 2:
                    raise HTTPException(403, "Injected second-recipient authorization failure")
            return original(db, channel, twin_id, *args, **kwargs)

        monkeypatch.setattr(product, "accept_request", fail_second)
    response = continue_request(product, monkeypatch, chat, request["causal_chain_id"])
    assert response.status_code == (403 if fault == "second_recipient" else 409), response.text
    with product.transaction() as db:
        pending = db.get(Message, pending_id)
        assert pending.state == "pending" and pending.seq is None
        assert receipts(db, pending_id) == []
        assert db.get(Chat, chat).next_message_seq == next_sequence
        assert db.scalar(select(func.count()).select_from(ReplyRequest)) == receiver_count
        chain = db.get(CausalChain, request["causal_chain_id"])
        assert chain.spent == chain.allowance == 6


def test_continue_can_publish_original_scheduled_owner_work_without_new_authority(product, monkeypatch):
    from app import scheduled_tasks
    chat = approved_chat(product, "duet-scheduled-pending")
    task = scheduled_tasks.write(product, "owner-alex", values={"title": "Saved coordination",
        "instructions": "Report these authorized updates to the approved peer", "timezone": "UTC",
        "schedule": {"kind": "interval", "seconds": 60}})
    request_id = scheduled_tasks.run_now(product, "owner-alex", task["id"], uuid4().hex)["request_id"]
    request, context = product.get_request(request_id), product.get_runtime_context(request_id)
    sends = [send(product, context, chat, f"Saved task update {index}") for index in range(7)]
    response = continue_request(product, monkeypatch, chat, request["causal_chain_id"])
    assert response.status_code == 200, response.text
    with product.transaction() as db:
        final = db.get(Message, sends[-1]["message_id"])
        assert final.state == "published" and final.seq is not None
        assert len(receipts(db, final.id)) == 1
        assert db.get(CausalChain, request["causal_chain_id"]).spent == 7


@pytest.mark.parametrize("restriction", ["withdrawn_private_source", "private_reset", "connection_closed"])
def test_pending_prepared_final_revalidates_original_reads_and_consent(product, monkeypatch, restriction):
    from duet_test_support import request_duet_reply

    from app.models import AllowanceGrant

    chat = approved_chat(product, "duet-final-revalidation")
    another_channel = approved_chat(product, "duet-shared-send-budget")
    incoming = append_duet(product, chat, "owner-maya", "Coordinate the authorized result")
    request = request_duet_reply(product, "owner-maya", chat, incoming["id"], "twin-alex")
    context = product.get_runtime_context(request["id"])
    private = product.send_message("owner-alex", "private-alex", "Owner's authorized reference", uuid4().hex)["message"]
    read = product._runtime_tool_transaction("history", {"chat_id": "private-alex"},
        {**context, "operation_id": uuid4().hex})
    assert private["id"] in {row["id"] for row in read["messages"]}
    # Use the real shared chain across channels, without changing counters by hand.
    for index in range(5):
        assert send(product, context, another_channel, f"Authorized update {index}")["sent"]
    pending_id = product.publish_runtime_output(request["id"], "Prepared final awaiting send capacity",
        request["session_id"], "final-run", "final-event", 1)
    with product.transaction() as db:
        draft = db.get(Message, pending_id)
        assert draft.state == "pending" and draft.seq is None and receipts(db, pending_id) == []
        assert db.get(CausalChain, request["causal_chain_id"]).spent == 6
        next_sequence = db.get(Chat, chat).next_message_seq
        receiver_count = db.scalar(select(func.count()).select_from(ReplyRequest))
    if restriction == "withdrawn_private_source":
        with product.transaction() as db:
            db.get(Message, private["id"]).state = "withdrawn"
    elif restriction == "private_reset":
        product.personal_reset("owner-alex", uuid4().hex, 0)
    else:
        duet_conversations.close_conversation(product, "owner-maya", chat)
    response = continue_request(product, monkeypatch, chat, request["causal_chain_id"])
    assert response.status_code == 409, response.text
    with product.transaction() as db:
        draft = db.get(Message, pending_id)
        assert draft.state != "published" and draft.seq is None and receipts(db, pending_id) == []
        assert db.get(Chat, chat).next_message_seq == next_sequence
        assert db.scalar(select(func.count()).select_from(ReplyRequest)) == receiver_count
        chain = db.get(CausalChain, request["causal_chain_id"])
        assert chain.spent == chain.allowance == 6
        assert not db.scalar(select(AllowanceGrant.id).where(AllowanceGrant.causal_chain_id == chain.id))


def test_preloaded_chain_across_concurrent_channels_cannot_publish_a_seventh_send(product):
    import threading

    first_chat = approved_chat(product, "duet-preloaded-first")
    second_chat = approved_chat(product, "duet-preloaded-second")
    request, context = origin(product)
    for index in range(5):
        assert send(product, context, first_chat, f"Earlier send {index}")["sent"]
    preloaded = threading.Barrier(2)
    first_committed = threading.Event()

    def publish(channel, first):
        try:
            with product.transaction() as db:
                # Keep a strong reference: SQLAlchemy must retain the stale identity.
                cached_chain = db.get(CausalChain, request["causal_chain_id"])
                assert cached_chain.spent == 5
                preloaded.wait(5)
                if not first:
                    assert first_committed.wait(5)
                    assert cached_chain.spent == 5, "This transaction must exercise a preloaded stale object"
                target = db.scalar(select(Chat).where(Chat.id == channel).with_for_update())
                message = product.append_prepared_message(db, target, db.get(ReplyRequest, request["id"]),
                    author_id="twin-alex", kind="twin", body="Sixth send" if first else "Seventh send")
                message_id = message.id
            return message_id
        finally:
            if first:
                first_committed.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        sixth = pool.submit(publish, first_chat, True)
        seventh = pool.submit(publish, second_chat, False)
        sixth_id, seventh_id = sixth.result(10), seventh.result(10)
    with product.transaction() as db:
        published, pending = db.get(Message, sixth_id), db.get(Message, seventh_id)
        assert published.state == "published" and published.seq is not None
        assert len(receipts(db, sixth_id)) == 1
        assert pending.state == "pending" and pending.seq is None and receipts(db, seventh_id) == []
        chain = db.get(CausalChain, request["causal_chain_id"])
        assert chain.spent == chain.allowance == 6
        assert db.scalar(select(func.count()).select_from(Message).where(
            Message.causal_chain_id == chain.id, Message.chat_id.in_([first_chat, second_chat]),
            Message.state == "published")) == 6


def test_continue_with_preloaded_allowance_rejects_an_already_superseded_owner_intent(product, monkeypatch):
    from contextlib import contextmanager

    from app.duet_messages import continue_sends
    from app.models import AllowanceGrant

    chat = approved_chat(product, "duet-preloaded-continue")
    request, context = origin(product)
    sends = [send(product, context, chat, f"Before continuation {index}") for index in range(7)]
    with product.transaction() as db:
        cached_chain = db.get(CausalChain, request["causal_chain_id"])
        assert cached_chain.allowance == 6
        # A second transaction commits an owner-approved Continue while this one
        # retains its preloaded object. The later stale owner intent must fail.
        first = continue_sends(product, "owner-maya", chat, cached_chain.id, uuid4().hex, 6)
        assert first["allowance"] == 12 and first["spent"] == 7
        assert cached_chain.allowance == 6
        next_sequence = db.get(Chat, chat).next_message_seq
        receiver_count = db.scalar(select(func.count()).select_from(ReplyRequest))

        @contextmanager
        def retained_transaction():
            yield db

        with monkeypatch.context() as patch:
            patch.setattr(product, "transaction", retained_transaction)
            with pytest.raises(HTTPException) as denied:
                continue_sends(product, "owner-maya", chat, cached_chain.id, uuid4().hex, 6)
        assert denied.value.status_code == 409
        assert db.get(Chat, chat).next_message_seq == next_sequence
        assert db.scalar(select(func.count()).select_from(ReplyRequest)) == receiver_count
        assert (cached_chain.spent, cached_chain.allowance) == (7, 12)
        assert db.scalar(select(func.count()).select_from(AllowanceGrant).where(
            AllowanceGrant.causal_chain_id == cached_chain.id)) == 1
        final = db.get(Message, sends[-1]["message_id"])
        assert final.state == "published" and len(receipts(db, final.id)) == 1
