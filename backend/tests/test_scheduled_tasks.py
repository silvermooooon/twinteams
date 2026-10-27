"""Real PostgreSQL schedule intents; no network models, executor, or application database writes."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import scheduled_tasks as schedules
from app import service as service_module
from app.models import (
    Approval,
    CausalChain,
    Chat,
    DispatchCommand,
    ReplyRequest,
    ScheduledTask,
    now,
)
from checks.postgres import TestDatabase


@pytest.fixture
def product(monkeypatch):
    database = TestDatabase()
    engine = create_engine(database.url.replace("postgresql://", "postgresql+psycopg://"))
    monkeypatch.setattr(service_module, "engine", engine)
    monkeypatch.setattr(service_module, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(service_module, "settings", replace(service_module.settings, database_url=database.url))
    product = service_module.ProductService()
    product.initialize(create_schema=True)
    yield product
    product.db_pool.shutdown()
    engine.dispose()
    database.close()


def create(product, **changes):
    return schedules.write(product, "owner-alex", values={"title": "Read updates", "instructions": "Review current updates and report meaningful changes.",
        "schedule": {"kind": "interval", "seconds": 60}, "timezone": "Asia/Shanghai", **changes})


def due(product, task_id, moment=None):
    moment = moment or now() - timedelta(seconds=5)
    with product.transaction() as db:
        row = db.get(ScheduledTask, task_id)
        row.next_run_at = moment
    return moment


@pytest.mark.parametrize(("schedule", "zone", "after", "expected"), [
    ({"kind": "daily", "time": "02:30"}, "America/New_York", "2026-03-08T05:00:00+00:00", "2026-03-08T07:30:00+00:00"),
    ({"kind": "daily", "time": "01:30"}, "America/New_York", "2026-11-01T04:00:00+00:00", "2026-11-01T05:30:00+00:00"),
    ({"kind": "daily", "time": "01:30"}, "America/New_York", "2026-11-01T05:31:00+00:00", "2026-11-02T06:30:00+00:00"),
    ({"kind": "weekly", "time": "09:00", "weekdays": [0]}, "Asia/Shanghai", "2026-10-23T01:00:00+00:00", "2026-10-26T01:00:00+00:00"),
])
def test_timezone_dst_schedule(schedule, zone, after, expected):
    assert schedules.next_run(schedule, zone, datetime.fromisoformat(after)).isoformat() == expected


def test_once_local_and_interval_coalescing():
    schedule = schedules.validate({"kind": "once", "at": "2026-03-08T02:30"}, "America/New_York")
    assert schedule["at"] == "2026-03-08T07:30:00+00:00"
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    assert schedules.next_run({"kind": "interval", "seconds": 60}, "UTC", anchor + timedelta(seconds=601), anchor=anchor) == anchor + timedelta(seconds=660)


@pytest.mark.parametrize("schedule,zone", [({"kind": "interval", "seconds": 1}, "UTC"),
    ({"kind": "interval", "seconds": True}, "UTC"), ({"kind": "daily", "time": "25:10"}, "UTC"),
    ({"kind": "weekly", "time": "10:00", "weekdays": [True]}, "UTC"),
    ({"kind": "daily", "time": "10:00"}, "Unknown/Zone")])
def test_schedule_validation(schedule, zone):
    with pytest.raises(HTTPException) as error:
        schedules.validate(schedule, zone)
    assert error.value.status_code == 422


def test_default_system_owner_crud_and_reserved_fields(product):
    initial = schedules.list_tasks(product, "owner-alex")["items"]
    assert len(initial) == 1 and initial[0]["kind"] == "system_review"
    assert now() < datetime.fromisoformat(initial[0]["next_run_at"]) <= now() + timedelta(hours=4)
    assert schedules.list_tasks(product, "owner-alex")["items"][0]["id"] == initial[0]["id"]
    task = create(product)
    assert task["timezone"] == "Asia/Shanghai"
    with pytest.raises(HTTPException) as error:
        schedules.write(product, "owner-maya", task["id"], {"enabled": False})
    assert error.value.status_code == 404
    for patch in ({"instructions": "Untrusted"}, {"title": "Other"}, {"schedule": {"kind": "daily", "time": "09:00"}}):
        with pytest.raises(HTTPException):
            schedules.write(product, "owner-alex", initial[0]["id"], patch)
    assert not schedules.write(product, "owner-alex", initial[0]["id"], {"enabled": False})["enabled"]
    assert schedules.write(product, "owner-alex", initial[0]["id"], {"schedule": {"kind": "interval", "seconds": 7200}})["schedule"]["seconds"] == 7200
    with pytest.raises(HTTPException):
        schedules.write(product, "owner-alex", initial[0]["id"], delete=True)
    assert schedules.write(product, "owner-alex", task["id"], delete=True) == {"ok": True}
    assert len(schedules.list_tasks(product, "owner-alex")["items"]) == 1


def test_atomic_occurrence_and_coalescing_restarts(product):
    task = create(product)
    planned = due(product, task["id"], now() - timedelta(minutes=40))
    first = schedules.process_due(product)
    assert len(first) == 1
    assert schedules.process_due(product) == []
    with product.transaction() as db:
        row = db.get(ScheduledTask, task["id"])
        request = db.get(ReplyRequest, first[0])
        assert now() < row.next_run_at <= now() + timedelta(seconds=60)
        assert schedules.snapshot(request)["planned_at"] == schedules.iso(planned)
        assert request.source == "scheduled_task" and request.source_message_id is None
        assert db.get(DispatchCommand, request.id)
        assert db.get(CausalChain, request.causal_chain_id).spent == 1
        assert request.execution_config is None
    restarted = service_module.ProductService()
    try:
        assert schedules.process_due(restarted) == []
    finally:
        restarted.db_pool.shutdown()
    context = product.get_runtime_context(first[0])
    assert context["scheduled_task"]["instructions"] == task["instructions"]
    assert context["input_body"] == task["instructions"]
    assert len(schedules.history(product, "owner-alex", task["id"])["items"]) == 1


def test_two_admissions_do_not_overlap_or_duplicate(product):
    task = create(product)
    due(product, task["id"])
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: schedules.process_due(product), range(2)))
    assert sum(map(len, results)) == 1
    request_id = next(result[0] for result in results if result)
    due(product, task["id"])
    assert schedules.process_due(product) == []
    with pytest.raises(HTTPException):
        schedules.write(product, "owner-alex", task["id"], delete=True)
    product.finish_request(request_id, "completed")
    assert len(schedules.process_due(product)) == 1


def test_pause_edit_once_and_manual_idempotence(product):
    task = create(product)
    first = schedules.run_now(product, "owner-alex", task["id"], "manual-key")
    assert schedules.run_now(product, "owner-alex", task["id"], "manual-key")["request_id"] == first["request_id"]
    with pytest.raises(HTTPException):
        schedules.run_now(product, "owner-alex", task["id"], "other-key")
    paused = schedules.write(product, "owner-alex", task["id"], {"enabled": False})
    assert not paused["enabled"] and paused["next_run_at"] is None
    product.finish_request(first["request_id"], "completed")
    once_at = now() + timedelta(minutes=3)
    edited = schedules.write(product, "owner-alex", task["id"], {"schedule": {"kind": "once", "at": once_at.isoformat()}, "enabled": True})
    assert edited["revision"] == task["revision"] + 2
    due(product, task["id"])
    assert len(schedules.process_due(product, moment=once_at + timedelta(seconds=1))) == 1
    with product.transaction() as db:
        assert db.get(ScheduledTask, task["id"]).next_run_at is None
    assert schedules.history(product, "owner-alex", task["id"])["items"][-1]["trigger"] == "manual"


def test_approval_wait_keeps_occurrence_unresolved(product):
    task = create(product)
    request_id = schedules.run_now(product, "owner-alex", task["id"], uuid4().hex)["request_id"]
    with product.transaction() as db:
        db.add(Approval(id=uuid4().hex, chat_id="private-alex", approver_id="owner-alex", title="Confirm", description="", target="", impact="", sdk_reference={"request_id": request_id}))
    product.finish_request(request_id, "completed")
    due(product, task["id"])
    assert schedules.process_due(product) == []


def test_system_empty_review_silent_and_reset_preserves_choice(product):
    system = schedules.list_tasks(product, "owner-alex")["items"][0]
    due(product, system["id"])
    assert schedules.process_due(product) == []
    silent = schedules.list_tasks(product, "owner-alex")["items"][0]
    assert silent["last_status"] == "silent" and silent["last_run_at"]
    task = create(product)
    schedules.write(product, "owner-alex", system["id"], {"enabled": False})
    with product.transaction() as db:
        chat = db.get(Chat, "private-alex")
        chat.reset_generation += 1
        schedules.reset_owner(db, "owner-alex", chat.reset_generation)
    items = {item["id"]: item for item in schedules.list_tasks(product, "owner-alex")["items"]}
    assert not items[task["id"]]["enabled"] and not items[system["id"]]["enabled"]
    assert schedules.write(product, "owner-alex", task["id"], {"enabled": True})["enabled"]


def test_runtime_schedule_tool_owner_idempotence_and_cannot_self_modify(product):
    human = product.send_message("owner-alex", "private-alex", "Every hour review my updates", uuid4().hex)["request"]
    context = product.get_runtime_context(human["id"])
    context["operation_id"] = uuid4().hex
    args = {"title": "Review", "instructions": "Check updates", "timezone": "UTC", "schedule": {"kind": "interval", "seconds": 3600}}
    result = product._runtime_tool_transaction("create_scheduled_task", args, context)
    assert product._runtime_tool_transaction("create_scheduled_task", args, context)["id"] == result["id"]
    scheduled_request = schedules.run_now(product, "owner-alex", result["id"], uuid4().hex)["request_id"]
    scheduled_context = product.get_runtime_context(scheduled_request)
    scheduled_context["operation_id"] = uuid4().hex
    with pytest.raises(HTTPException) as error:
        product._runtime_tool_transaction("update_scheduled_task", {"id": result["id"], "enabled": False}, scheduled_context)
    assert error.value.status_code == 403


def test_runtime_preflight_blocks_delegation_after_parent_terminal(product):
    task = create(product)
    request_id = schedules.run_now(product, "owner-alex", task["id"], uuid4().hex)["request_id"]
    product.finish_request(request_id, "completed")
    due(product, task["id"])
    class Runtime:
        async def has_unresolved_scheduled_work(self, request):
            return request["id"] == request_id
    product.runtime = Runtime()
    assert asyncio.run(schedules.admit_due(product)) == []


def test_admission_rolls_back_request_and_advance_together(product, monkeypatch):
    task = create(product)
    planned = due(product, task["id"])
    original = product.accept_request
    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("crash before commit")
    monkeypatch.setattr(product, "accept_request", crash)
    with pytest.raises(RuntimeError):
        schedules.process_due(product)
    with product.transaction() as db:
        assert db.get(ScheduledTask, task["id"]).next_run_at == planned
        assert db.scalar(select(func.count()).select_from(ReplyRequest).where(ReplyRequest.source == "scheduled_task")) == 0
    monkeypatch.setattr(product, "accept_request", original)
    assert len(schedules.process_due(product)) == 1


def test_frozen_config_and_quiet_continuation_keep_owner_tools(product, monkeypatch):
    from app import personal
    monkeypatch.setattr(personal, "service", product)
    task = create(product)
    request_id = schedules.run_now(product, "owner-alex", task["id"], uuid4().hex)["request_id"]
    config = personal.freeze_execution_config(request_id, {"version": 1, "workspace": "/temporary", "skills": [], "credentials": [], "execution_enabled": True})
    assert config["execution_enabled"] and config["scheduled_task"]["id"] == task["id"]
    with product.transaction() as db:
        origin = db.get(ReplyRequest, request_id)
        continuation = product.accept_request(db, db.get(Chat, "private-alex"), origin.target_twin_id,
            "owner-alex", "approval_result", uuid4().hex, causal_chain_id=origin.causal_chain_id)
        continuation.execution_config = config
        assert product.request_json(db, origin, "owner-alex")["quiet_background"]
        assert product.request_json(db, continuation, "owner-alex")["quiet_background"]
        continuation_id = continuation.id
    product.finish_request(request_id, "completed")
    item = next(item for item in schedules.list_tasks(product, "owner-alex")["items"] if item["id"] == task["id"])
    assert item["pending_work"] and item["active_request_id"] == continuation_id
    product.finish_request(continuation_id, "completed")
    item = next(item for item in schedules.list_tasks(product, "owner-alex")["items"] if item["id"] == task["id"])
    assert not item["pending_work"] and item["active_request_id"] is None


def test_review_includes_twin_progress_cutoff_and_deduplicates_notice(product):
    human = product.send_message("owner-alex", "private-alex", "Watch the delivery deadline", uuid4().hex)["request"]
    product.get_runtime_context(human["id"])
    product.bind_runtime_run(human["id"], "private-session", "ordinary-run")
    product.publish_runtime_output(human["id"], "Delivery work made progress", "private-session", "ordinary-run", "ordinary-event")
    product.finish_request(human["id"], "completed")
    system = schedules.list_tasks(product, "owner-alex")["items"][0]
    due(product, system["id"])
    request_id = schedules.process_due(product)[0]
    context = product.get_runtime_context(request_id)
    assert any(item["kind"] == "twin" for item in context["system_review"]["changes"])
    cutoff = context["system_review"]["as_of"]
    product.bind_runtime_run(request_id, "private-session", "review-run")
    message_id = product.publish_runtime_output(request_id, "Delivery deadline is now tomorrow", "private-session", "review-run", "review-event")
    assert message_id
    assert product.publish_runtime_output(request_id, "Delivery deadline is now tomorrow", "private-session", "review-run", "duplicate-content-event") is None
    product.send_message("owner-alex", "private-alex", "A new delivery update arrived", uuid4().hex)
    product.finish_request(request_id, "completed")
    with product.transaction() as db:
        row = db.get(ScheduledTask, system["id"])
        assert row.details["last_successful_cutoff"] == cutoff
        review = schedules.review_snapshot(product, db, row, now())
        assert review["needs_review"] and review["changes"]
        assert not any(item["id"] == message_id for item in review["changes"])


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.sorted = {}
        self.fail = False

    async def exists(self, key):
        if self.fail:
            raise ConnectionError("Redis unavailable")
        return key in self.values

    async def set(self, key, value):
        self.values[key] = value

    def pipeline(self, **kwargs):
        parent = self
        class Pipeline:
            async def __aenter__(self):
                self.commands = []
                return self
            async def __aexit__(self, *args):
                return False
            def zadd(self, key, values):
                self.commands.append(("add", key, values))
            def zrem(self, key, *members):
                self.commands.append(("remove", key, members))
            async def execute(self):
                if parent.fail:
                    raise ConnectionError("Redis unavailable")
                for action, key, value in self.commands:
                    if action == "add":
                        parent.sorted.setdefault(key, {}).update(value)
                    else:
                        for member in value:
                            parent.sorted.setdefault(key, {}).pop(member, None)
        return Pipeline()

    async def __aenter__(self):
        self.commands = []
        return self

    async def __aexit__(self, *args):
        return False

    def zadd(self, key, values):
        self.commands.append((key, values))

    async def execute(self):
        if self.fail:
            raise ConnectionError("Redis unavailable")
        for key, values in self.commands:
            self.sorted.setdefault(key, {}).update(values)

    async def zrem(self, key, *members):
        for member in members:
            self.sorted.setdefault(key, {}).pop(member, None)

    async def zrangebyscore(self, key, low, high, start=0, num=100):
        return [member for member, score in sorted(self.sorted.get(key, {}).items(), key=lambda item: item[1]) if score <= float(high)][start:start+num]


def test_redis_outage_rebuild_stale_hint_and_idle_no_database_scan(product, monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(schedules.notifications, "redis", client)
    index = schedules.DueIndex(product)
    task = create(product)
    due(product, task["id"])
    client.fail = True
    asyncio.run(index.tick())
    assert len(schedules.history(product, "owner-alex", task["id"])["items"]) == 1
    client.fail = False
    # Simulate the crash between DB commit and Redis publication with a fresh worker.
    index = schedules.DueIndex(product)
    asyncio.run(index.tick())
    asyncio.run(index.tick())
    with product.transaction() as db:
        row = db.get(ScheduledTask, task["id"])
        assert not row.index_dirty
        assert row.id in client.sorted[index.key]
    original_db_call = product.db_call
    calls = []
    async def observed(*args, **kwargs):
        calls.append(args[0])
        return await original_db_call(*args, **kwargs)
    monkeypatch.setattr(product, "db_call", observed)
    asyncio.run(index.tick())
    assert calls == []
    # A stale due hint cannot revive a paused task.
    schedules.write(product, "owner-alex", task["id"], {"enabled": False})
    client.sorted[index.key][task["id"]] = 0
    asyncio.run(index.tick())
    assert len(schedules.history(product, "owner-alex", task["id"])["items"]) == 1
    # Losing Redis entirely rebuilds by bounded PostgreSQL primary-key pages.
    client.sorted.clear()
    client.values.clear()
    asyncio.run(index.tick())
    assert client.values[index.key + ":ready"] == "1"


def test_reminder_alone_eligible_and_foreign_chain_request_not_cancellable(product):
    from app.models import Reminder
    system = schedules.list_tasks(product, "owner-alex")["items"][0]
    with product.transaction() as db:
        reminder = Reminder(owner_id="owner-alex", title="Deadline tomorrow", due_at=now() + timedelta(days=1))
        db.add(reminder)
    due(product, system["id"])
    request_id = schedules.process_due(product)[0]
    context = product.get_runtime_context(request_id)
    assert context["system_review"]["deadlines"][0]["title"] == "Deadline tomorrow"
    with product.transaction() as db:
        root = db.get(ReplyRequest, request_id)
        from duet_test_support import approved_chat
        approved_chat(product, "duet-alex-maya")
        group = db.get(Chat, "duet-alex-maya")
        foreign = product.accept_request(db, group, "twin-maya", "owner-alex", "auto", uuid4().hex, causal_chain_id=root.causal_chain_id)
        assert foreign.target_twin_id != root.target_twin_id
    product.finish_request(request_id, "completed")
    item = next(item for item in schedules.list_tasks(product, "owner-alex")["items"] if item["id"] == system["id"])
    assert item["pending_work"] and item["active_request_id"] is None


def test_retry_completion_clears_old_failure_and_restores_due_hint(product):
    task = create(product)
    request_id = schedules.run_now(product, "owner-alex", task["id"], uuid4().hex)["request_id"]
    product.finish_request(request_id, "failed", "Original transient failure")
    retry = product.retry_request("owner-alex", request_id, uuid4().hex)
    product.finish_request(retry["id"], "completed")
    item = next(item for item in schedules.list_tasks(product, "owner-alex")["items"] if item["id"] == task["id"])
    assert item["last_status"] == "completed" and item["last_error"] is None
    with product.transaction() as db:
        assert db.get(ScheduledTask, task["id"]).index_dirty


def test_revoked_group_snapshot_does_not_retain_prose_before_first_run(product):
    from app.models import Membership
    with product.transaction() as db:
        group_id = db.scalar(select(Chat.id).where(Chat.kind == "group"))
    source = product.send_message("owner-alex", group_id, "Group secret should not remain in a frozen review", uuid4().hex)["message"]
    result = product._runtime_tool_transaction("create_tracking", {"title": "Restricted group plan", "description": "Group secret", "source_message_ids": [source["id"]]},
        {"owner_id": "owner-alex", "chat": {"id": group_id}, "operation_id": uuid4().hex})
    system = schedules.list_tasks(product, "owner-alex")["items"][0]
    request_id = schedules.run_now(product, "owner-alex", system["id"], uuid4().hex)["request_id"]
    with product.transaction() as db:
        member = db.scalar(select(Membership).where(Membership.chat_id == group_id, Membership.owner_id == "owner-alex"))
        member.left_at = now()
    context = product.get_runtime_context(request_id)
    assert not any(item["id"] == result["id"] for item in context["tracking"])
    assert "tracking" not in context["system_review"]
    assert "body" not in context["system_review"]["previous_notice"]
    assert "Restricted group plan" not in str(context["scheduled_task"])
