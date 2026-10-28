"""Activity display policy on isolated PostgreSQL; execution and journals stay intact."""
import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from test_memory_dream import product as product  # noqa: PLC0414

from app import api
from app import service as service_module
from app.activity_visibility_schema import upgrade_activity_visibility_schema
from app.models import ActivityProjection, Chat, Message, ReplyRequest, now
from app.runtime_bridge import RuntimeBridge


def request(product, source="private", *, origin=None, owner="owner-alex", session=None, run=None):
    with product.transaction() as db:
        chat = db.get(Chat, "private-" + owner.removeprefix("owner-"))
        snapshot = {"id": uuid4().hex, "kind": "system_review" if source == "system_review" else "personal",
                    "instructions": "Review the authorized fixture task", "title": "Fixture task"}
        row = product.accept_request(db, chat, product.twin(db, owner).id, owner, source, uuid4().hex,
            input_body=json.dumps(snapshot) if source in {"scheduled_task", "system_review"} else "Fixture task",
            continuation_of_request_id=origin)
        if session:
            row.session_id = session
        row.run_id = run or uuid4().hex
        return {"id": row.id, "session_id": row.session_id, "run_id": row.run_id}


def projection(product, *, request=None, visible=True, source="user", parent=None, owner="owner-alex", offset=0, status="running"):
    sid, rid = (request["session_id"], request["run_id"]) if request else (uuid4().hex, uuid4().hex)
    product.record_activity("private-" + owner.removeprefix("owner-"), owner, sid, rid,
        {"source": source, "parent_run_id": parent, "user_visible": visible, "status": status,
         "title": "Synthetic activity", "started_at": now() + timedelta(seconds=offset)})
    return sid, rid


def ids(product, **kwargs):
    return {(row["session_id"], row["run_id"]) for row in product.activity_page("owner-alex", "private-alex", **kwargs)["items"]}


@pytest.mark.parametrize("source,visible", [("private", True), ("auto", True), ("scheduled_task", False), ("system_review", False)])
def test_context_classifies_origin_and_continuations_without_using_background_as_visibility(product, source, visible):
    original = request(product, source)
    assert product.get_runtime_context(original["id"])["activity_user_visible"] is visible
    first = request(product, "approval_result", origin=original["id"])
    second = request(product, "credential_result", origin=first["id"])
    # Existing accepted execution facts may label a continuation simply as user.
    assert product.get_runtime_context(second["id"])["activity_user_visible"] is visible
    if source == "auto":
        assert product.get_request(original["id"])["background"] is True
    row = projection(product, request=original, visible=visible, status="failed")
    product.record_activity("private-alex", "owner-alex", *row,
        {"status": "completed", "summary": "A later update must preserve classification"})
    with product.transaction() as db:
        assert db.get(ActivityProjection, row).user_visible is visible
    assert (row in ids(product)) is visible


def test_normal_private_context_does_not_issue_an_ancestry_lookup(product):
    original = request(product)
    from test_context_acceptance import sql_reads

    assert product.get_runtime_context(original["id"])["activity_user_visible"] is True
    with product.transaction() as db:
        row = db.get(ReplyRequest, original["id"])
        with sql_reads() as reads:
            assert product._activity_request_user_visible(db, row) is True
        assert reads == [], "Classifying an already-loaded ordinary root needs no ancestry SQL"


def test_public_pages_filter_before_limit_and_hidden_detail_never_reads_journal(product, monkeypatch):
    older = projection(product, offset=0)
    newer = projection(product, offset=20)
    hidden = [projection(product, visible=False, source="system_review", offset=40 + index) for index in range(8)]
    store_calls = []
    async def read(tenant, session, run, **kwargs):
        store_calls.append((session, run, kwargs))
        return [{"id": "event", "seq": 1, "time": 1, "type": "run_completed", "data": {}}]
    runtime = RuntimeBridge.__new__(RuntimeBridge)
    runtime.service = product
    runtime.store = SimpleNamespace(read_run_records=read)
    product.runtime = runtime
    monkeypatch.setattr(api, "service", product)
    owner = ["owner-alex"]
    application = FastAPI(); application.include_router(api.router)
    application.dependency_overrides[api.identity] = lambda: owner[0]
    with TestClient(application) as client:
        page = client.get("/api/chats/private-alex/activity", params={"limit": 1}).json()
        assert [(row["session_id"], row["run_id"]) for row in page["items"]] == [newer]
        assert page["has_more"] and page["next_cursor"][1:] == list(newer)
        page2 = client.get("/api/chats/private-alex/activity", params={"limit": 1, "before": json.dumps(page["next_cursor"])}).json()
        assert [(row["session_id"], row["run_id"]) for row in page2["items"]] == [older]
        assert not page2["has_more"] and page2["next_cursor"] is None
        for sid, rid in hidden:
            assert client.get(f"/api/chats/private-alex/activity/{sid}/{rid}", params={"after_seq": 0, "user_visible_only": False}).status_code == 404
        assert store_calls == []
        sid, rid = newer
        detail = client.get(f"/api/chats/private-alex/activity/{sid}/{rid}")
        assert detail.status_code == 200 and detail.json()["events"][0]["type"] == "run_completed"
        owner[0] = "owner-maya"
        assert client.get("/api/chats/private-alex/activity").status_code == 403
        assert client.get(f"/api/chats/private-maya/activity/{sid}/{rid}").status_code == 404
    # Internal coordination sees hidden work, but retains every ownership fence.
    assert len(asyncio.run(runtime.activity("private-alex", "owner-alex"))) == 10
    assert asyncio.run(runtime.get_activity("private-alex", "owner-alex", *hidden[0]))["run"]
    with pytest.raises(HTTPException) as denied:
        asyncio.run(runtime.get_activity("private-alex", "owner-maya", *hidden[0]))
    assert denied.value.status_code == 403
    with product.transaction() as db:
        stale = ActivityProjection(session_id=uuid4().hex, run_id=uuid4().hex,
            chat_id="private-alex", owner_id="owner-alex", private_generation=1,
            user_visible=False, title="Stale generation")
        db.add(stale); db.flush(); stale_key = (stale.session_id, stale.run_id)
    before = len(store_calls)
    with pytest.raises(HTTPException) as denied:
        asyncio.run(runtime.get_activity("private-alex", "owner-alex", *stale_key))
    assert denied.value.status_code == 404 and len(store_calls) == before


def test_legacy_upgrade_is_scoped_run_specific_idempotent_and_keeps_messages(product):
    same_session = uuid4().hex
    system = request(product, "system_review", session=same_session)
    human = request(product, session=same_session)
    continuation = request(product, "approval_result", origin=system["id"])
    hidden = {projection(product, request=system), projection(product, request=continuation)}
    hidden.add(projection(product, source="subagent", parent=system["session_id"] + "/" + system["run_id"]))
    parent = system["run_id"]
    for _ in range(3):
        child = projection(product, source="subagent", parent=parent)
        hidden.add(child); parent = child[1]
    hidden.add(projection(product, source="memory_dream"))
    visible = {projection(product, request=human), projection(product, source="subagent", parent=human["run_id"])}
    visible.add(projection(product, source="subagent", parent="missing-historical-parent"))
    # A foreign owner's system root must not hide this owner's child with the
    # same historical run label. A stale generation must not supply ancestry.
    projection(product, request={"session_id": uuid4().hex, "run_id": human["run_id"]}, source="system_review", owner="owner-maya")
    with product.transaction() as db:
        db.add(ActivityProjection(session_id=uuid4().hex, run_id=human["run_id"], chat_id="private-alex",
            owner_id="owner-alex", private_generation=1, source="system_review", title="Other generation"))
        message = product.append_message(db, db.get(Chat, "private-alex"), author_id="twin-alex", kind="twin", body="Keep this proactive result")
        preserved = message.id
    with service_module.engine.begin() as connection:
        connection.execute(text("ALTER TABLE tt_activity_runs DROP COLUMN user_visible"))
    with pytest.raises(RuntimeError, match="activity|init_db"):
        product.check_initialized()
    upgrade_activity_visibility_schema(service_module.engine)
    with product.transaction() as db:
        assert all(db.get(ActivityProjection, key).user_visible is False for key in hidden)
        assert all(db.get(ActivityProjection, key).user_visible is True for key in visible)
        assert db.get(Message, preserved).body == "Keep this proactive result"
    statements = []
    def after(conn, cursor, statement, parameters, context, many):
        statements.append(statement.lower())
    event.listen(service_module.engine, "after_cursor_execute", after)
    try:
        upgrade_activity_visibility_schema(service_module.engine)
        product.check_initialized()
    finally:
        event.remove(service_module.engine, "after_cursor_execute", after)
    assert not any("update tt_activity_runs" in sql for sql in statements), "Repeated startup/upgrade must not re-backfill history"
    assert ids(product) == visible
    with service_module.engine.begin() as connection:
        connection.execute(text("DROP INDEX tt_activity_user_visible_order"))
    with pytest.raises(RuntimeError, match="activity"):
        product.check_initialized()
    upgrade_activity_visibility_schema(service_module.engine)
    product.check_initialized()


def test_legacy_scheduled_snapshot_marker_is_hidden_without_an_origin_or_background_flag(product):
    original = request(product, "approval_result")
    with product.transaction() as db:
        row = db.get(ReplyRequest, original["id"])
        row.background = False
        row.execution_config = {"scheduled_task": {"id": "legacy-task", "kind": "personal", "instructions": "Existing saved task"}}
    assert product.get_runtime_context(original["id"])["activity_user_visible"] is False


@pytest.mark.parametrize("scheduled", [False, True])
def test_actual_main_and_nested_child_factories_inherit_visibility_and_keep_final_messages(product, scheduled):
    from agent_runtime import AssistantMessageEventStream, assistant_message
    from agent_runtime.sessions import DatabaseSession

    from app import scheduled_tasks

    async def run():
        runtime = RuntimeBridge(product)
        calls = []
        def offline(model, context, options):
            calls.append(1)
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model,
                content=[{"type": "text", "text": "Verified synthetic result"}], stopReason="stop")})
            return stream
        runtime.models.register_api("offline-regression", offline)
        await runtime.start(); product.bind_runtime(runtime)
        if scheduled:
            task = scheduled_tasks.write(product, "owner-alex", values={"title": "Fixture schedule",
                "instructions": "Report the authorized result", "timezone": "Asia/Shanghai",
                "schedule": {"kind": "interval", "seconds": 60}})
            original = scheduled_tasks.run_now(product, "owner-alex", task["id"], uuid4().hex)["request_id"]
            saved_task = {key: task[key] for key in ("title", "instructions", "schedule", "timezone", "enabled", "revision")}
        else:
            original = product.send_message("owner-alex", "private-alex", "Authorized direct task", uuid4().hex)["request"]["id"]
        try:
            await product.execute_request(original)
            root = product.get_request(original)
            assert root["status"] == "completed"
            if scheduled:
                # A terminal replay can build the first projection without
                # agent_start. Its final status path must retain hidden scope.
                context = product.get_runtime_context(original, include_completed=True)
                with product.transaction() as db:
                    db.delete(db.get(ActivityProjection, (root["session_id"], root["run_id"])))
                before = len(calls)
                await runtime.execute(root, context)
                assert len(calls) == before
            parent_session, parent_run = root["session_id"], root["run_id"]
            for index in range(2):
                session = DatabaseSession(runtime.store, session_id=uuid4().hex, tenant_id=runtime.tenant_id, user_id="owner-alex")
                await session.commit("subagent_created", parent_session_id=parent_session, parent_run_id=parent_run,
                    root_run_id=root["session_id"] + "/" + root["run_id"], name="worker", task="Assigned synthetic subtask", operation_id=uuid4().hex)
                child = await runtime._child_factory(session)
                await child.prompt("Assigned synthetic subtask")
                assert child.state.error_message is None
                parent_session, parent_run = session.session_id, session.snapshot["run_id"]
            with product.transaction() as db:
                rows = list(db.scalars(select(ActivityProjection).where(ActivityProjection.owner_id == "owner-alex")))
                assert len(rows) == 3
                assert all(row.user_visible is (not scheduled) for row in rows)
                assert db.scalar(select(Message.id).where(Message.request_id == original, Message.body == "Verified synthetic result"))
            assert len(ids(product)) == (0 if scheduled else 3)
            if scheduled:
                current = next(item for item in scheduled_tasks.list_tasks(product, "owner-alex")["items"] if item["id"] == task["id"])
                assert {key: current[key] for key in saved_task} == saved_task
                assert scheduled_tasks.history(product, "owner-alex", task["id"])["items"][0]["status"] == "completed"
        finally:
            await runtime.close()
    asyncio.run(run())
