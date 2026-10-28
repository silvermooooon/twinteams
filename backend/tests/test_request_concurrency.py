"""Independent tasks overlap; a task's own continuations remain ordered and fenced."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, text
from test_memory_dream import product as product  # noqa: PLC0414

from app import scheduled_tasks as schedules
from app import service as service_module
from app.models import Chat, ReplyLane, ReplyRequest, ScheduledTask, now
from app.request_concurrency_schema import upgrade_request_concurrency_schema
from app.request_sessions import legacy_session_id, request_session_id


def accept(product, source="manual", owner="alex", origin=None):
    if source in {"scheduled_task", "system_review"}:
        task_id = schedules.system_id("owner-" + owner) if source == "system_review" else schedules.write(
            product, "owner-" + owner, values={"title": "Independent scheduled task", "instructions": "Review authorized updates",
                "schedule": {"kind": "interval", "seconds": 60}, "timezone": "UTC"})["id"]
        with product.transaction() as db:
            return schedules.enqueue(product, db, db.get(ScheduledTask, task_id), now(), now(), manual_key=uuid4().hex).id
    with product.transaction() as db:
        request = product.accept_request(db, db.get(Chat, "private-" + owner), "twin-" + owner,
            "owner-" + owner, source, uuid4().hex, input_body="A distinct task " + uuid4().hex,
            continuation_of_request_id=origin)
        return request.id


def test_two_private_user_roots_execute_before_either_finishes(product):
    async def run():
        first = product.send_message("owner-alex", "private-alex", "First independent task", uuid4().hex)["request"]
        second = product.send_message("owner-alex", "private-alex", "Second independent task", uuid4().hex)["request"]
        assert first["session_id"] == request_session_id(first["id"])
        assert first["session_id"] != second["session_id"]
        entered, release = set(), asyncio.Event()

        async def execute(request, context):
            await product.db_call("bind_runtime_run", request["id"], request["session_id"], "run-" + request["id"])
            entered.add(request["id"])
            await release.wait()
            return {"session_id": request["session_id"], "run_id": "run-" + request["id"], "body": "Result " + context["source_message"]["body"]}

        product.bind_runtime(SimpleNamespace(configured=True, startup_error=None, execute=execute))
        assert set(product.claim_dispatches()) == {first["id"], second["id"]}
        try:
            for request_id in (first["id"], second["id"]):
                product.tasks[request_id] = asyncio.create_task(product.execute_request(request_id))
            async with asyncio.timeout(5):
                while len(entered) != 2:
                    await asyncio.sleep(0.01)
            assert product.get_request(first["id"])["status"] == "running"
            assert product.get_request(second["id"])["status"] == "running"
        finally:
            tasks = tuple(product.tasks.values())
            release.set()
            await asyncio.gather(*tasks)
        detail = product.chat_detail("owner-alex", "private-alex")
        assert {m["body"] for m in detail["messages"] if m["kind"] == "twin"} == {
            "Result First independent task", "Result Second independent task"}
    asyncio.run(run())


@pytest.mark.parametrize("source", ["scheduled_task", "system_review", "memory_dream"])
def test_background_running_does_not_block_same_owner_human(product, source):
    background = accept(product, source)
    saved = product.get_request(background)
    product.bind_runtime_run(background, saved["session_id"], "background-run")
    product.tasks[background] = object()
    try:
        second_background = accept(product, "scheduled_task")
        human = product.send_message("owner-alex", "private-alex", "Reply now", uuid4().hex)["request"]
        selected = product.claim_dispatches()
        assert human["id"] in selected
        assert second_background not in selected
        assert product.get_request(background)["status"] == "running"
    finally:
        product.tasks.clear()


def test_continuations_wait_only_for_own_origin_and_session_order(product):
    original = accept(product)
    first = accept(product, "approval_result", origin=original)
    second = accept(product, "credential_result", origin=original)
    unrelated = accept(product)
    assert set(product.claim_dispatches()) == {original, unrelated}
    session = product.get_request(original)["session_id"]
    assert product.get_request(first)["session_id"] == product.get_request(second)["session_id"] == session
    product.finish_request(original, "completed")
    assert first in product.claim_dispatches() and second not in product.claim_dispatches()
    product.finish_request(first, "completed")
    assert second in product.claim_dispatches()
    assert product.get_request(unrelated)["status"] == "queued"


def test_background_continuation_inherits_limit_and_does_not_relabel_as_human(product):
    original = accept(product, "system_review")
    continuation = accept(product, "approval_result", origin=original)
    product.finish_request(original, "completed")
    assert product.get_request(continuation)["background"] is True
    assert product.get_request(continuation)["continuation_of_request_id"] == original
    request = product.get_request(continuation)
    product.bind_runtime_run(continuation, request["session_id"], "continued-background")
    product.tasks[continuation] = object()
    try:
        blocked = accept(product, "scheduled_task")
        human = accept(product)
        assert human in product.claim_dispatches()
        assert blocked not in product.claim_dispatches()
    finally:
        product.tasks.clear()


def test_peer_duet_work_keeps_a_slot_for_its_owners_new_private_message(product):
    from duet_test_support import append_duet, approved_chat, request_duet_reply
    chat_id = approved_chat(product, "duet-concurrency")
    first_message = append_duet(product, chat_id, "owner-maya", "First peer request")
    first = request_duet_reply(product, "owner-maya", chat_id, first_message["id"], "twin-alex")
    assert first["background"] is True
    product.bind_runtime_run(first["id"], first["session_id"], "peer-run")
    product.tasks[first["id"]] = object()
    try:
        second_message = append_duet(product, chat_id, "owner-maya", "Second peer request")
        second = request_duet_reply(product, "owner-maya", chat_id, second_message["id"], "twin-alex")
        human = product.send_message("owner-alex", "private-alex", "Reply to me while the peer is working", uuid4().hex)["request"]
        assert human["background"] is False
        selected = product.claim_dispatches()
        assert human["id"] in selected and second["id"] not in selected
    finally:
        product.tasks.clear()


def test_background_quota_is_independent_of_foreground_single_slot(product, monkeypatch):
    monkeypatch.setattr(service_module, "settings", replace(service_module.settings, max_running=3, max_per_owner=2, max_background_running=2, max_background_per_owner=1))
    backgrounds = [accept(product, "system_review", owner) for owner in ("alex", "maya", "jordan")]
    selected = product.claim_dispatches()
    assert len(selected) == 2 and set(selected).issubset(backgrounds)
    product.tasks.update({request_id: object() for request_id in selected})
    try:
        human = accept(product)
        assert product.claim_dispatches() == [human]
    finally:
        product.tasks.clear()
    monkeypatch.setattr(service_module, "settings", replace(service_module.settings, max_running=1, max_per_owner=1))
    product.finish_request(human, "completed")
    assert len(product.claim_dispatches()) == 2


def test_cancel_one_parallel_root_preserves_other_request_and_output(product):
    first, second = accept(product), accept(product)
    for request_id in (first, second):
        product.get_runtime_context(request_id)
        product.bind_runtime_run(request_id, product.get_request(request_id)["session_id"], "run-" + request_id)
    product._cancel_intent("owner-alex", first)
    assert product.get_request(first)["cancel_requested"]
    assert not product.get_request(second)["cancel_requested"]
    assert product.publish_runtime_output(first, "Late cancelled output", product.get_request(first)["session_id"], "run-" + first) is None
    product.complete_request(second, body="Independent result", session_id=product.get_request(second)["session_id"], run_id="run-" + second)
    assert product.get_request(second)["status"] == "completed"


def test_legacy_null_session_fifo_does_not_block_new_root(product):
    legacy_first, legacy_second = accept(product), accept(product)
    old_session = legacy_session_id("private-alex", "twin-alex", 0)
    with product.transaction() as db:
        db.get(ReplyRequest, legacy_first).session_id = old_session
        db.get(ReplyRequest, legacy_second).session_id = None
        db.get(ReplyLane, ("private-alex", "twin-alex")).session_id = old_session
    fresh = accept(product)
    assert set(product.claim_dispatches()) == {legacy_first, fresh}
    product.finish_request(legacy_first, "completed")
    assert legacy_second in product.claim_dispatches()


def test_context_includes_completed_root_with_live_child_without_internal_prompt(product):
    original = product.send_message("owner-alex", "private-alex", "Prepare the original report", uuid4().hex)["request"]
    product.bind_runtime_run(original["id"], original["session_id"], "original-run")
    product.finish_request(original["id"], "completed")
    product.record_activity("private-alex", "owner-alex", "child-session", "child-run", {
        "source": "subagent", "status": "running", "parent_run_id": "original-run", "title": "INTERNAL WORKER PROMPT"})
    fresh = accept(product)
    context = product.get_runtime_context(fresh)
    prior = next(row for row in context["active_requests"] if row["id"] == original["id"])
    assert prior["input"] == "Prepare the original report"
    assert prior["status"] == "completed"
    assert prior["children"] == [{"session_id": "child-session", "run_id": "child-run", "status": "running"}]
    assert "INTERNAL WORKER PROMPT" not in str(context["active_requests"])
    other = accept(product, owner="maya")
    assert product.get_runtime_context(other)["active_requests"] == []


def test_additive_upgrade_preserves_existing_requests_and_execution_identity(product):
    first, second = accept(product, "system_review"), accept(product)
    with product.transaction() as db:
        before = sorted((row.id, row.session_id, row.status) for row in db.scalars(select(ReplyRequest)))
    with service_module.engine.begin() as connection:
        connection.execute(text("ALTER TABLE tt_reply_requests DROP COLUMN background"))
        connection.execute(text("ALTER TABLE tt_reply_requests DROP COLUMN continuation_of_request_id"))
    upgrade_request_concurrency_schema(service_module.engine)
    upgrade_request_concurrency_schema(service_module.engine)
    with product.transaction() as db:
        assert sorted((row.id, row.session_id, row.status) for row in db.scalars(select(ReplyRequest))) == before
        assert db.get(ReplyRequest, first).background and not db.get(ReplyRequest, second).background
    assert {"continuation_of_request_id", "background"}.issubset({column["name"] for column in inspect(service_module.engine).get_columns("tt_reply_requests")})
    with service_module.engine.begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM pg_indexes WHERE schemaname=current_schema() AND indexname='tt_request_session_order'")).scalar() == 1
