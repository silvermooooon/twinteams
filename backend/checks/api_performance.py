"""Successful API budgets on disposable PostgreSQL/Redis and non-empty history.

Run from backend: .venv/bin/python -m checks.api_performance --report /tmp/api-load.json
No application data or live provider is used. Each measured request must return
the expected data within three seconds; timeouts/errors do not pass the budget.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from checks.local_stack import environment
from checks.postgres import TestDatabase


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    local = environment("api")
    os.environ["TWINTEAMS_TEST_DB_URL"] = local["DATABASE_URL"]
    database = TestDatabase().configure()
    temporary = tempfile.TemporaryDirectory(prefix="duet-api-budget-")
    namespace = "duet-api-budget-" + uuid4().hex
    os.environ.update(
        TWINTEAMS_ENV="local", TWINTEAMS_DEVELOPMENT_MODE="true",
        TWINTEAMS_TENANT_ID="api-performance", TWINTEAMS_REDIS_PREFIX=namespace,
        TWINTEAMS_REDIS_URL=local["TWINTEAMS_REDIS_URL"],
        AGENT_MEMORY_DIR=temporary.name + "/memory",
        TWINTEAMS_WORKSPACE_ROOT=temporary.name + "/workspace",
        AGENT_API="offline-regression", OPENAI_API_KEY="offline-no-network",
        TWINTEAMS_MESSAGE_CENTER_URL="", TWINTEAMS_MESSAGE_CENTER_TOKEN="",
    )
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from redis import Redis
    from sqlalchemy import event

    from app import (
        api,
        contact_api,
        credential_api,
        onboarding_api,
        personal_api,
        question_api,
        scheduled_task_api,
    )
    from app.db import engine
    from app.notifications import notifications
    from app.service import service
    from checks.chat_history_fixture import seed_chat_history

    @asynccontextmanager
    async def lifespan(app):
        await notifications.start()
        yield
        await notifications.close()

    app = FastAPI(lifespan=lifespan)
    for module in (api, personal_api, contact_api, scheduled_task_api,
                   credential_api, onboarding_api, question_api):
        app.include_router(module.router)
    statements = []

    def before_sql(*_args):
        statements.append(1)

    report = {"budget_ms": 3000, "provider_calls": 0,
              "shape": {"history_messages": 5000, "human_chats": 100,
                        "pending_questions": 100, "approvals": 100,
                        "shared_files": 100, "git_memory_entries": 100, "activity_runs": 100,
                        "tracking_topics": 10, "updates_per_topic": 3},
              "single": [], "concurrent": []}
    try:
        service.initialize()
        seed = seed_chat_history(service, 5000)
        seed_peripheral_data(service, seed)
        event.listen(engine, "before_cursor_execute", before_sql)
        with TestClient(app) as client:
            assert client.post("/api/auth/login", json={"username": "DEV-AAA"}).status_code == 200

            def measure(path):
                started = time.perf_counter_ns()
                response = client.get(path)
                elapsed = (time.perf_counter_ns() - started) / 1_000_000
                assert response.status_code == 200, (path, response.status_code)
                assert_fixture_response(path, response.json(), seed)
                row = {"path": path, "status": response.status_code,
                       "elapsed_ms": round(elapsed, 3), "response_bytes": len(response.content)}
                return row

            paths = [
                "/api/bootstrap", "/api/chats/private-alex?limit=30",
                "/api/chats/private-alex/summary", "/api/chats/private-alex/activity",
                "/api/chats/private-alex/tracking",
                "/api/chats/private-alex/tracking/budget-topic-0",
                "/api/chats/group-alex-maya/shared-files", "/api/chats/private-alex/workspace",
                "/api/me/twin/memory?limit=100", "/api/me/twin/memory/search?q=Synthetic",
                "/api/me/twin/contacts", "/api/me/twin/scheduled-tasks", "/api/settings",
            ]
            for path in paths:
                statements.clear()
                row = measure(path)
                row["pg_statements"] = len(statements)
                report["single"].append(row)
                print(json.dumps(row), flush=True)
            for label, requests in (
                ("ten_history_reads", [paths[1]] * 10),
                ("ten_mixed_pages", [paths[i] for i in (0, 1, 4, 6, 8, 0, 1, 4, 6, 8)]),
            ):
                statements.clear()
                with ThreadPoolExecutor(max_workers=10) as pool:
                    rows = list(pool.map(measure, requests))
                result = {"case": label, "clients": 10,
                          "pg_statements_total": len(statements), "requests": rows}
                report["concurrent"].append(result)
                print(json.dumps(result), flush=True)
        rows = [*report["single"], *(row for case in report["concurrent"] for row in case["requests"])]
        report["over_budget"] = [row for row in rows if row["elapsed_ms"] > 3000]
        assert not report["over_budget"], f"{len(report['over_budget'])} successful requests exceeded 3000 ms"
    finally:
        if event.contains(engine, "before_cursor_execute", before_sql):
            event.remove(engine, "before_cursor_execute", before_sql)
        service.db_pool.shutdown()
        engine.dispose()
        database.close()
        temporary.cleanup()
        with Redis.from_url(local["TWINTEAMS_REDIS_URL"]) as redis:
            keys = list(redis.scan_iter(match=namespace + "*"))
            if keys:
                redis.delete(*keys)
            remaining = len(list(redis.scan_iter(match=namespace + "*")))
        report["cleanup"] = {"schema_removed": database.closed,
                             "files_removed": not Path(temporary.name).exists(),
                             "redis_remaining": remaining}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2))


def assert_fixture_response(path, data, seed):
    """A faster empty or truncated response must fail the same load scenario."""
    if path == "/api/bootstrap":
        rooms = [row for row in data["chats"] if row["title"].startswith("Synthetic room ")]
        assert len(rooms) == 100 and len({row["id"] for row in rooms}) == 100
        assert all(set(row["member_owner_ids"]) == {"owner-alex", "owner-maya", "owner-jordan"} for row in rooms)
        assert all(row["last_message"].startswith("Synthetic room message ") for row in rooms)
    elif path == "/api/chats/private-alex?limit=30":
        assert len(data["messages"]) == 30 and data["has_more"]
        assert data["messages"][-1]["id"] == seed["latest_message_id"]
        assert len(data["questions"]) == 30
        assert {row["message_id"] for row in data["questions"]} == {row["id"] for row in data["messages"]}
        assert {row["id"] for row in data["approvals"]} == {f"budget-approval-{i}" for i in range(100)}
    elif path == "/api/chats/private-alex/summary":
        assert data["last_message_meta"]["id"] == seed["latest_message_id"]
    elif path == "/api/chats/private-alex/activity":
        assert len(data["items"]) == 50 and data["has_more"]
        assert all(row["session_id"].startswith("budget-activity-") for row in data["items"])
    elif path == "/api/chats/private-alex/tracking":
        assert {row["id"] for row in data["tracking"]} == {f"budget-topic-{i}" for i in range(10)}
    elif path == "/api/chats/private-alex/tracking/budget-topic-0":
        assert data["id"] == "budget-topic-0"
        assert {row["body"] for row in data["updates"]} == {f"Synthetic update {i}" for i in range(3)}
    elif path == "/api/chats/group-alex-maya/shared-files":
        assert {row["id"] for row in data["files"]} == {f"budget-file-{i}" for i in range(100)}
    elif path == "/api/chats/private-alex/workspace":
        assert any(row["name"] == "budget-generated.md" and row["kind"] == "file" for row in data)
    elif path == "/api/me/twin/memory?limit=100":
        assert {row["key"] for row in data["items"]} == {f"projects/project-{i}" for i in range(100)}
        assert data["next_cursor"] is None
    elif path == "/api/me/twin/memory/search?q=Synthetic":
        assert len(data["items"]) == 20
        assert all(row["key"].startswith("projects/project-") for row in data["items"])
    elif path == "/api/me/twin/contacts":
        assert any(row["owner_id"] == "owner-maya" for row in data["items"])
    elif path == "/api/me/twin/scheduled-tasks":
        assert any(row["kind"] == "system_review" for row in data["items"])
    elif path == "/api/settings":
        assert "default_reply_prompt" in data


def seed_peripheral_data(service, seed):
    """Populate real persisted shapes, avoiding empty-list performance claims."""
    from agent_runtime.memory import MemorySource
    from sqlalchemy import select

    from app import personal
    from app.models import (
        ActivityProjection,
        Approval,
        Chat,
        ChatAttachment,
        ProductOperation,
        ReplyRequest,
        TrackingTopic,
        TrackingUpdate,
    )
    from app.question_models import QuestionRequest

    for index in range(100):
        chat = service.create_chat("owner-alex", ["owner-maya", "owner-jordan"],
                                   f"Synthetic room {index}")
        service.send_message("owner-maya", chat["id"], f"Synthetic room message {index}", uuid4().hex)
    with service.transaction() as db:
        private = db.get(Chat, "private-alex")
        requests = list(db.scalars(select(ReplyRequest.id).where(
            ReplyRequest.chat_id == private.id).order_by(ReplyRequest.accepted_seq.desc()).limit(100)))
        for index, request_id in enumerate(requests):
            db.add(ActivityProjection(session_id=f"budget-activity-{index}", run_id=f"budget-run-{index}",
                chat_id=private.id, owner_id="owner-alex", status="completed", title="Synthetic completed run"))
            db.add(Approval(id=f"budget-approval-{index}", chat_id=private.id,
                approver_id="owner-alex", title="Synthetic approval", description="Synthetic decision",
                target="fixture", impact="fixture", sdk_reference={"request_id": request_id}, source_message_ids=[]))
            message = service.append_message(db, private, author_id="twin-alex", kind="twin",
                body=f"Synthetic pending question {index}", request_id=request_id)
            db.add(QuestionRequest(owner_id="owner-alex", chat_id=private.id, origin_chat_id=private.id,
                origin_request_id=request_id, message_id=message.id,
                runtime_session_id=f"budget-session-{index}", runtime_run_id=f"budget-run-{index}",
                runtime_command_id=request_id, question="Which synthetic option?", options=["A", "B"]))
        # The final history page contains genuine pending controls as well as messages.
        seed["latest_message_id"] = message.id
        shared = db.get(Chat, "group-alex-maya")
        for index in range(100):
            message = service.append_message(db, shared, author_id="owner-alex", kind="human",
                body=f"Synthetic file delivery {index}")
            aid = f"budget-file-{index}"
            db.add(ChatAttachment(id=aid, chat_id=shared.id, owner_id="owner-alex",
                name=f"review-{index}.md", mime_type="text/markdown", kind="file", size=20,
                sha256="a" * 64, relative_path=f"group-alex-maya/review-{index}.md", message_id=message.id))
            db.add(ProductOperation(operation_id=f"budget-share-{index}", chat_id=shared.id,
                owner_id="owner-alex", name="present_files", arguments_hash="b" * 64,
                result={"shared_files": [{"attachment_id": aid, "peer_owner_ids": ["owner-maya"]}]}))
        for index in range(10):
            topic = f"budget-topic-{index}"
            sources = [f"performance-message-{seed['first_seq'] + 4999 - index * 2}"]
            db.add(TrackingTopic(id=topic, chat_id=private.id, title=f"Synthetic work {index}",
                description="Synthetic next step", source_message_ids=sources, source_floor_seq=1, subject_key=topic))
            for child in range(3):
                db.add(TrackingUpdate(topic_id=topic, body=f"Synthetic update {child}",
                                     source_message_ids=sources, source_floor_seq=1))

    async def seed_memory():
        store = personal.own_memory_store("owner-alex")
        async with store.draft() as draft:
            for index in range(100):
                await draft.write(f"projects/project-{index}", f"Synthetic project {index} responsibility.",
                    sources=[MemorySource("budget-source", str(index))], expected_version=None)
            await draft.publish("budget-seed")

    asyncio.run(seed_memory())
    workspace = personal.resolve_twin_workspace("owner-alex")
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "budget-generated.md").write_text("# Synthetic generated deliverable\n")


if __name__ == "__main__":
    main()
