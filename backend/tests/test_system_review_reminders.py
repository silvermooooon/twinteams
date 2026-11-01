"""System Review reads current, owner-scoped Dream failures in disposable PostgreSQL."""
import json
from uuid import uuid4

from sqlalchemy import inspect, text
from test_memory_dream import completed, due
from test_memory_dream import product as product  # noqa: PLC0414

from app import scheduled_tasks as schedules
from app import service as service_module
from app.models import MemoryDreamState, ReplyRequest, ScheduledTask, now
from app.personal_schema import upgrade_personal_schema


def test_failure_remains_through_retry_and_cancel_until_confirmed_publication(product):
    completed(product)
    dream_id = due(product)
    before = now()
    product.fail_request(dream_id, "Publication failed\n" + "detail " * 100)
    after = now()
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        failed_at, error = state.last_failure_at, state.last_error
        assert before <= failed_at <= after
        reminder = schedules.relevant_reminders(db, "owner-alex")[0]
        assert reminder["failed_at"] == schedules.iso(failed_at)
        assert len(reminder["context"]) == 300 and "\n" not in reminder["context"]
        system = schedules.ensure_system(product, db, "owner-alex")
        review = schedules.review_snapshot(product, db, system, now())
        assert review["needs_review"] and review["relevant_reminders"] == [reminder]
        other = schedules.ensure_system(product, db, "owner-maya")
        assert schedules.review_snapshot(product, db, other, now())["relevant_reminders"] == []
    retry_id = due(product)
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        assert state.last_status == "queued" and state.last_failure_at == failed_at
        assert schedules.relevant_reminders(db, "owner-alex") == [reminder]
    product._cancel_intent("owner-alex", retry_id)
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        assert state.last_status == "cancelled" and state.last_failure_at == failed_at
        assert state.last_error == error
        assert schedules.relevant_reminders(db, "owner-alex") == [reminder]
    next_id = due(product)
    request = product.get_request(next_id)
    payload = product.get_runtime_context(next_id)["memory_dream"]
    product.bind_runtime_run(next_id, request["session_id"], "retry-run")
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        assert state.last_status == "running" and state.last_failure_at == failed_at
        assert schedules.relevant_reminders(db, "owner-alex") == [reminder]
    product.complete_request(next_id, memory_dream={"published": True, "version": "verified",
        "cursor_before": payload["cursor_before"], "through_seq": payload["through_seq"]})
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        assert state.last_status == "published" and state.last_error is None
        assert state.last_failure_at is None
        assert schedules.relevant_reminders(db, "owner-alex") == []


def test_queued_review_refreshes_recovered_failure_without_rewriting_frozen_occurrence(product):
    completed(product)
    dream_id = due(product)
    before = now()
    product.mark_memory_dream_recovery(dream_id, "Unknown publication")
    product.fail_request(dream_id, "Unknown publication")
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        assert before <= state.last_failure_at <= now()
        task = schedules.ensure_system(product, db, "owner-alex")
        review = schedules.enqueue(product, db, task, now(), now(), manual_key=uuid4().hex)
        review_id = review.id
        frozen = json.loads(review.input_body)
        assert frozen["system_review"]["relevant_reminders"]
        review.execution_config = {"version": 0, "scheduled_task": frozen}
    product.confirm_memory_dream_publication(dream_id, "verified-after-unknown")
    context = product.get_runtime_context(review_id)
    assert context["system_review"]["relevant_reminders"] == []
    assert context["scheduled_task"]["system_review"] == context["system_review"]
    assert context["scheduled_task"]["occurrence_id"] == frozen["occurrence_id"]
    with product.transaction() as db:
        assert db.get(ReplyRequest, dream_id).status == "failed"
        review = db.get(ReplyRequest, review_id)
        assert json.loads(review.input_body) == frozen
        assert review.execution_config["scheduled_task"] == frozen
        task = db.get(ScheduledTask, frozen["id"])
        assert schedules.review_snapshot(product, db, task, now())["relevant_reminders"] == []


def test_cancellation_is_not_a_failure_and_legacy_failure_time_stays_unknown(product):
    completed(product)
    dream_id = due(product)
    product._cancel_intent("owner-alex", dream_id)
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        assert state.last_status == "cancelled" and state.last_failure_at is None
        assert schedules.relevant_reminders(db, "owner-alex") == []
        state.last_status, state.last_error = "failed", "Legacy failure"
    with product.transaction() as db:
        assert schedules.relevant_reminders(db, "owner-alex") == [{
            "kind": "memory_dream_failure", "failed_at": None, "context": "Legacy failure"}]


def test_failure_time_column_is_added_by_existing_personal_migration(product):
    with service_module.engine.begin() as connection:
        connection.execute(text("ALTER TABLE tt_memory_dream_state DROP COLUMN last_failure_at"))
    upgrade_personal_schema(service_module.engine)
    upgrade_personal_schema(service_module.engine)
    columns = {column["name"]: column for column in inspect(service_module.engine).get_columns("tt_memory_dream_state")}
    assert columns["last_failure_at"]["nullable"]
