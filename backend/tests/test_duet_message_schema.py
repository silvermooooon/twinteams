"""The explicit breaking upgrade cleans retired state once, on disposable PG."""
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, text
from test_memory_dream import product as product  # noqa: PLC0414

from app import service as service_module
from app.duet_message_schema import (
    RETIRED_TABLES,
    check_duet_message_schema,
    upgrade_duet_message_schema,
)
from app.models import (
    AllowanceGrant,
    CausalChain,
    DispatchCommand,
    Message,
    PendingChildResult,
    ReplyRequest,
)


def request(product, body):
    return product.send_message("owner-alex", "private-alex", body, uuid4().hex)["request"]


def test_fresh_schema_checks_and_repeat_upgrade_preserve_current_send_accounting(product):
    row = request(product, "Current owner intent")
    with product.transaction() as db:
        chain = db.get(CausalChain, row["causal_chain_id"])
        chain.spent, chain.allowance = 7, 12
        db.add(AllowanceGrant(causal_chain_id=chain.id, chat_id="private-alex", owner_id="owner-alex",
            client_request_key="current-grant", amount=6, base_allowance=6))
    for _ in range(2):
        upgrade_duet_message_schema(service_module.engine)
        check_duet_message_schema(inspect(service_module.engine))
    with product.transaction() as db:
        chain = db.get(CausalChain, row["causal_chain_id"])
        assert (chain.spent, chain.allowance) == (7, 12)
        assert db.scalar(select(AllowanceGrant).where(AllowanceGrant.client_request_key == "current-grant"))
    with service_module.engine.begin() as connection:
        connection.execute(text("DROP INDEX tt_pending_duet_sends"))
    with pytest.raises(RuntimeError, match="explicit Duet message schema"):
        check_duet_message_schema(inspect(service_module.engine))
    upgrade_duet_message_schema(service_module.engine)
    check_duet_message_schema(inspect(service_module.engine))


@pytest.mark.parametrize("pause_reason", ["budget_exhausted", "repeated_content"])
def test_old_upgrade_cancels_only_retired_work_preserves_messages_and_releases_budget_paused_children(product, pause_reason):
    old_auto, old_followup, completed, current = [request(product, body) for body in (
        "Old peer work", "Old waiting continuation", "Completed old task", "Current owner work")]
    child_id = uuid4().hex
    with product.transaction() as db:
        db.get(ReplyRequest, old_auto["id"]).source = "auto"
        old = db.get(ReplyRequest, old_followup["id"])
        old.source, old.status = "followup_result", "running"
        old = db.get(ReplyRequest, completed["id"])
        old.source, old.status = "auto", "completed"
        chain = db.get(CausalChain, current["causal_chain_id"])
        chain.spent, chain.allowance = 7, 12
        db.add(AllowanceGrant(causal_chain_id=chain.id, chat_id="private-alex", owner_id="owner-alex",
            client_request_key="retired-grant", amount=6, base_allowance=6))
        db.add(PendingChildResult(id=child_id, causal_chain_id=chain.id, parent_request_id=current["id"],
            chat_id="private-alex", owner_id="owner-alex", requester_id="owner-alex", target_twin_id="twin-alex",
            body="Durable child work must remain available", notification={}, state="paused", pause_reason=pause_reason))
        before = [(m.id, m.body, m.seq, m.state) for m in db.scalars(select(Message).order_by(Message.id))]
    with service_module.engine.begin() as connection:
        for table in RETIRED_TABLES:
            connection.execute(text(f"CREATE TABLE {table} (id text PRIMARY KEY)"))
        connection.execute(text("ALTER TABLE tt_reply_requests ADD COLUMN automatic_reservation_id varchar(80)"))
        connection.execute(text("ALTER TABLE tt_messages ALTER COLUMN seq SET NOT NULL"))
        connection.execute(text("ALTER TABLE tt_messages DROP CONSTRAINT tt_message_state"))
        connection.execute(text("ALTER TABLE tt_messages ADD CONSTRAINT tt_message_state CHECK (state IN ('published','withdrawn'))"))
    with pytest.raises(RuntimeError):
        check_duet_message_schema(inspect(service_module.engine))
    upgrade_duet_message_schema(service_module.engine)
    check_duet_message_schema(inspect(service_module.engine))
    with product.transaction() as db:
        for previous in (old_auto, old_followup):
            row = db.get(ReplyRequest, previous["id"])
            assert row.status == "cancelled" and row.cancel_requested
            assert db.get(DispatchCommand, row.id).state == "delivered"
        assert db.get(ReplyRequest, completed["id"]).status == "completed"
        assert db.get(ReplyRequest, current["id"]).status == "queued"
        assert db.scalar(select(AllowanceGrant).where(AllowanceGrant.client_request_key == "retired-grant")) is None
        assert (db.get(CausalChain, current["causal_chain_id"]).spent, db.get(CausalChain, current["causal_chain_id"]).allowance) == (0, 6)
        child = db.get(PendingChildResult, child_id)
        assert child.state == "pending" and child.pause_reason is None
        assert child.body == "Durable child work must remain available"
        assert [(m.id, m.body, m.seq, m.state) for m in db.scalars(select(Message).order_by(Message.id))] == before
        db.get(CausalChain, current["causal_chain_id"]).spent = 1
    upgrade_duet_message_schema(service_module.engine)
    with product.transaction() as db:
        assert db.get(CausalChain, current["causal_chain_id"]).spent == 1


def test_forum_upgrade_is_additive_repeatable_and_keeps_pending_drafts(product):
    from duet_test_support import approved_chat

    from app.duet_forum_schema import check_duet_forum_schema, upgrade_duet_forum_schema
    from app.models import Chat

    chat = approved_chat(product, 'duet-migration')
    with product.transaction() as db:
        chain = product.new_causal_chain(db, 'owner-alex')
        published = product.append_message(db, db.get(Chat, chat), author_id='twin-alex',
            kind='twin', body='Existing published post', causal_chain_id=chain.id)
        pending = Message(chat_id=chat, author_id='twin-alex', kind='twin', body='Unsent legacy draft',
            causal_chain_id=chain.id, seq=None, state='pending')
        db.add(pending)
        db.flush()
        published_id, pending_id = published.id, pending.id
        before_requests = list(db.scalars(select(ReplyRequest.id).order_by(ReplyRequest.id)))
    with service_module.engine.begin() as connection:
        connection.execute(text('DROP TABLE tt_duet_post_revisions'))
        connection.execute(text('ALTER TABLE tt_messages DROP COLUMN mention_owner_ids, DROP COLUMN revision, DROP COLUMN edited_at'))
        connection.execute(text('ALTER TABLE tt_duet_conversations DROP COLUMN opening_mention_owner_ids, DROP COLUMN revision'))
    with pytest.raises(RuntimeError, match='forum schema'):
        check_duet_forum_schema(inspect(service_module.engine))
    for _ in range(2):
        upgrade_duet_forum_schema(service_module.engine)
        check_duet_forum_schema(inspect(service_module.engine))
    with product.transaction() as db:
        published, pending = db.get(Message, published_id), db.get(Message, pending_id)
        assert published.body == 'Existing published post' and published.state == 'published'
        assert published.revision == 1 and published.mention_owner_ids == [] and published.edited_at is None
        assert pending.body == 'Unsent legacy draft' and pending.state == 'pending' and pending.seq is None
        assert list(db.scalars(select(ReplyRequest.id).order_by(ReplyRequest.id))) == before_requests


def test_forum_upgrade_freezes_existing_file_audience_and_never_expands_it_on_repeat(product):
    from duet_test_support import approved_chat

    from app.duet_forum_schema import upgrade_duet_forum_schema
    from app.models import ChatAttachment, Membership

    chat = approved_chat(product, 'duet-file-migration')
    with product.transaction() as db:
        attachment = ChatAttachment(chat_id=chat, owner_id='owner-alex', name='Existing.txt',
            mime_type='text/plain', kind='file', size=10, sha256='a' * 64,
            relative_path='published/existing.txt')
        db.add(attachment)
        db.flush()
        attachment_id = attachment.id
    upgrade_duet_forum_schema(service_module.engine)
    with product.transaction() as db:
        assert db.get(ChatAttachment, attachment_id).granted_owner_ids == ['owner-alex', 'owner-maya']
        db.add(Membership(chat_id=chat, owner_id='owner-jordan', history_policy='all', lower_seq=1))
    upgrade_duet_forum_schema(service_module.engine)
    with product.transaction() as db:
        attachment = db.get(ChatAttachment, attachment_id)
        assert attachment.granted_owner_ids == ['owner-alex', 'owner-maya']
        assert attachment.relative_path == 'published/existing.txt' and attachment.sha256 == 'a' * 64
