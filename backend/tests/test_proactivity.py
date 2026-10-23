"""Owner review policy reuses actual schedule rows, FIFO and indexed due intent."""
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from test_credentials import product as product  # noqa: PLC0414

from app import proactivity
from app import scheduled_tasks as schedules
from app.models import ReplyRequest, ScheduledTask


def context(product):
    request = product.send_message("owner-alex", "private-alex", "Please change my review preferences", uuid4().hex)["request"]
    return product.get_runtime_context(request["id"]) | {"operation_id": uuid4().hex}


def update(product, values, current=None):
    return asyncio.run(product.runtime_tool("update_proactivity", values, current or context(product)))


def review(product):
    with product.transaction() as db:
        return db.get(ScheduledTask, schedules.system_id("owner-alex"))


def test_policy_single_truth_idempotent_save_does_not_create_or_change_user_tasks(product):
    initial = asyncio.run(product.runtime_tool("get_proactivity", {}, context(product)))
    assert initial == {"level": "normal", "quiet_hours": None, "review_interval_seconds": 14400}
    user = schedules.write(product, "owner-alex", values={"title": "Explicit reminder", "instructions": "Tell me the result",
        "timezone": "UTC", "schedule": {"kind": "interval", "seconds": 60}})
    current = context(product)
    values = {"level": "low", "quiet_hours": {"start": "22:00", "end": "08:00", "timezone": "Asia/Shanghai"}}
    result = update(product, values, current)
    assert result["level"] == "low" and result["review_interval_seconds"] == 14400
    before = review(product).revision
    assert update(product, values, current) == result and review(product).revision == before
    with pytest.raises(HTTPException) as caught:
        update(product, {"level": "high"}, current)
    assert caught.value.status_code == 409
    assert update(product, {"level": "off"})["level"] == "off"
    tasks = schedules.list_tasks(product, "owner-alex")["items"]
    assert len(tasks) == 2 and next(row for row in tasks if row["id"] == user["id"]) == user
    system = next(row for row in tasks if row["kind"] == "system_review")
    assert not system["enabled"] and system["proactivity"]["level"] == "off" and system["next_run_at"] is None
    schedules.write(product, "owner-alex", system["id"], {"enabled": True})
    assert asyncio.run(product.runtime_tool("get_proactivity", {}, context(product)))["level"] == "low"
    assert update(product, {"quiet_hours": None})["quiet_hours"] is None


@pytest.mark.parametrize("values", [
    {}, {"level": "wild"}, {"level": []}, {"quiet_hours": {"start": "22:00", "end": "08:00"}},
    {"quiet_hours": {"start": "22:00", "end": "22:00", "timezone": "UTC"}},
    {"quiet_hours": {"start": "25:00", "end": "08:00", "timezone": "UTC"}},
    {"quiet_hours": {"start": "22:00", "end": "08:00", "timezone": "Unknown/Timezone"}},
])
def test_invalid_policy_rejected_without_writes(product, values):
    before = review(product).revision
    with pytest.raises(HTTPException) as caught:
        update(product, values)
    assert caught.value.status_code == 422
    assert review(product).revision == before


@pytest.mark.parametrize("zone,start,end,moment,expected", [
    ("Asia/Shanghai", "22:00", "08:00", "2026-10-23T15:00:00+00:00", "2026-10-24T00:00:00+00:00"),
    ("Asia/Shanghai", "22:00", "08:00", "2026-10-23T23:59:00+00:00", "2026-10-24T00:00:00+00:00"),
    ("UTC", "09:00", "17:00", "2026-10-23T10:00:00+00:00", "2026-10-23T17:00:00+00:00"),
    ("UTC", "22:00", "08:00", "2026-10-23T08:00:00+00:00", None),
    ("America/New_York", "22:00", "02:30", "2026-03-08T06:00:00+00:00", "2026-03-08T07:30:00+00:00"),
    ("America/New_York", "22:00", "01:30", "2026-11-01T06:10:00+00:00", "2026-11-01T06:30:00+00:00"),
])
def test_quiet_hours_midnight_dst_and_boundaries(product, zone, start, end, moment, expected):
    update(product, {"quiet_hours": {"start": start, "end": end, "timezone": zone}})
    actual = proactivity.quiet_until(review(product), datetime.fromisoformat(moment))
    assert (actual.isoformat() if actual else None) == expected


def test_quiet_automatic_defers_without_model_or_new_request_manual_and_explicit_tasks_still_run(product):
    update(product, {"quiet_hours": {"start": "22:00", "end": "08:00", "timezone": "Asia/Shanghai"}})
    user = schedules.write(product, "owner-alex", values={"title": "Explicit recurring task", "instructions": "Provide my requested result",
        "timezone": "UTC", "schedule": {"kind": "interval", "seconds": 60}})
    moment = datetime(2026, 10, 23, 15, tzinfo=UTC)
    system_id = schedules.system_id("owner-alex")
    with product.transaction() as db:
        for task_id in (system_id, user["id"]):
            db.get(ScheduledTask, task_id).next_run_at = moment - timedelta(seconds=1)
    accepted = schedules.process_due(product, [system_id, user["id"]], moment=moment)
    assert len(accepted) == 1
    with product.transaction() as db:
        assert db.get(ReplyRequest, accepted[0]).source == "scheduled_task"
        row = db.get(ScheduledTask, system_id)
        assert row.next_run_at == datetime(2026, 10, 24, tzinfo=UTC) and row.index_dirty
    update(product, {"level": "off"})
    manual = schedules.run_now(product, "owner-alex", system_id, uuid4().hex)
    assert manual["request_id"]
    with product.transaction() as db:
        request = db.get(ReplyRequest, manual["request_id"])
        assert request.source == "system_review" and request.client_request_key.startswith("scheduled-now:")


def test_owner_private_origin_scheduled_deny_reset_policy_and_accepted_snapshot(product):
    values = {"level": "high", "quiet_hours": {"start": "22:00", "end": "08:00", "timezone": "UTC"}}
    update(product, values)
    system_id = schedules.system_id("owner-alex")
    manual = schedules.run_now(product, "owner-alex", system_id, uuid4().hex)
    before = product.get_request(manual["request_id"])
    assert schedules.snapshot(type("Request", (), before))["proactivity"]["level"] == "high"
    scheduled = product.get_runtime_context(manual["request_id"]) | {"operation_id": uuid4().hex}
    with pytest.raises(HTTPException) as denied:
        update(product, {"level": "off"}, scheduled)
    assert denied.value.status_code == 403
    group = context(product)
    with product.transaction() as db:
        chat, _ = product.membership(db, "owner-alex", "group-alex-maya")
        request = product.accept_request(db, chat, product.twin(db, "owner-alex").id, "owner-alex", "manual", uuid4().hex)
        group_id = request.id
    group = product.get_runtime_context(group_id) | {"operation_id": uuid4().hex}
    with pytest.raises(HTTPException):
        update(product, {"level": "off"}, group)
    update(product, {"level": "low"})
    assert schedules.snapshot(type("Request", (), before))["proactivity"]["level"] == "high", "Accepted review keeps its frozen policy"
    product.personal_reset("owner-alex", uuid4().hex, 0)
    assert review(product).details["proactivity"] == {"level": "low", "quiet_hours": values["quiet_hours"]}


def test_level_only_and_quiet_clear_keep_existing_review_cadence(product):
    planned = review(product).next_run_at
    update(product, {"level": "low"})
    assert review(product).next_run_at == planned
    update(product, {"level": "high"})
    assert review(product).next_run_at == planned
    update(product, {"quiet_hours": None})
    assert review(product).next_run_at == planned
