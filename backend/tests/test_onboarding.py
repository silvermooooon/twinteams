"""Real disposable PG first-meeting lifecycle; model decisions are not simulated claims."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from test_memory_dream import product as product  # noqa: PLC0414

from app import onboarding, onboarding_api, scheduled_tasks, twin_reset
from app.api import identity
from app.models import (
    Actor,
    ChangeOutbox,
    Chat,
    MemoryDreamState,
    Message,
    OnboardingState,
    ReplyRequest,
    ScheduledTask,
    now,
)
from app.runtime_bridge import RuntimeBridge
from app.twin_prompts import conversation_system


@pytest.fixture(autouse=True)
def isolated_settings(product, monkeypatch, tmp_path):
    configured = replace(onboarding.settings, onboarding_enabled=True, workspace_root=tmp_path / "workspace")
    monkeypatch.setattr(onboarding, "settings", configured)
    monkeypatch.setattr(twin_reset, "settings", configured)
    monkeypatch.setattr(onboarding_api, "service", product)


def send(product, owner="alex", body="Please draft a short release checklist"):
    return product.send_message("owner-" + owner, "private-" + owner, body, uuid4().hex)


def tool_context(product, request_id):
    context = product.get_runtime_context(request_id)
    session_id = product.get_request(request_id)["session_id"]
    return {**context, "request_source": "private", "request_requester_id": "owner-alex",
            "runtime_session_id": session_id, "main_session_id": session_id,
            "operation_id": "first-meeting-tool-" + uuid4().hex}


def test_persisted_welcome_idempotent_not_model_dream_or_review_input(product, monkeypatch):
    assert onboarding.get_state(product, "owner-alex")["status"] == "new"
    with product.transaction() as db:
        product.twin(db, "owner-alex").name = "Elysia"
        before_revision = db.get(Chat, "private-alex").revision
        before_outbox = db.scalar(select(func.count()).select_from(ChangeOutbox).where(ChangeOutbox.chat_id == "private-alex"))
    emitted = []
    from app.notifications import notifications
    monkeypatch.setattr(notifications, "emit", lambda chat_id, **kwargs: emitted.append((chat_id, kwargs)))
    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(lambda _: onboarding.start(product, "owner-alex"), range(2)))
    assert states[0] == states[1] and states[0]["status"] == "active"
    with product.transaction() as db:
        messages = list(db.scalars(select(Message).where(Message.chat_id == "private-alex")))
        assert len(messages) == 1 and messages[0].id == states[0]["welcome_message_id"]
        assert messages[0].kind == "system" and db.get(Actor, "owner-alex").name in messages[0].body
        assert "I'm Elysia, your Duet." in messages[0].body
        assert product.twin(db, "owner-alex").name == "Elysia"
        assert db.get(Chat, "private-alex").revision > before_revision
        assert db.scalar(select(func.count()).select_from(ChangeOutbox).where(ChangeOutbox.chat_id == "private-alex")) == before_outbox + 1
        assert emitted == [("private-alex", {"work": False})]
        assert messages[0].body.count("?") == 1
        assert db.scalar(select(func.count()).select_from(ReplyRequest)) == 0
        assert db.get(MemoryDreamState, "owner-alex") is None
        task = db.scalar(select(ScheduledTask).where(ScheduledTask.owner_id == "owner-alex"))
        assert not scheduled_tasks.review_snapshot(product, db, task, now())["needs_review"]
    assert onboarding.get_state(product, "owner-maya")["status"] == "new"


def test_first_send_wins_and_concurrent_start_never_inserts_late_welcome(product):
    send(product)
    assert onboarding.start(product, "owner-alex")["status"] == "unavailable"
    with product.transaction() as db:
        assert db.get(OnboardingState, "owner-alex") is None
    with ThreadPoolExecutor(max_workers=2) as pool:
        opened = pool.submit(onboarding.start, product, "owner-maya")
        posted = pool.submit(send, product, "maya")
        opened.result(), posted.result()
    with product.transaction() as db:
        kinds = list(db.scalars(select(Message.kind).where(Message.chat_id == "private-maya").order_by(Message.seq)))
        assert kinds in [["human"], ["system", "human"]]


@pytest.mark.parametrize("terminal", ["completed", "skipped"])
def test_finish_or_skip_survives_refresh_reset_and_does_not_complete_actual_task(product, terminal):
    onboarding.start(product, "owner-alex")
    request = send(product)["request"]
    context = tool_context(product, request["id"])
    if terminal == "skipped":
        result = onboarding.skip(product, "owner-alex")
    else:
        result = asyncio.run(product.runtime_tool(onboarding.TOOL, {"status": terminal}, context))
        assert asyncio.run(product.runtime_tool(onboarding.TOOL, {"status": terminal}, context)) == result
    assert result["status"] == terminal
    assert product.get_request(request["id"])["status"] == "queued"
    assert onboarding.get_state(product, "owner-alex")["status"] == terminal
    reset = product.personal_reset("owner-alex", uuid4().hex, 0)
    assert reset["status"] == "completed"
    assert onboarding.start(product, "owner-alex")["status"] == terminal
    with product.transaction() as db:
        assert db.get(OnboardingState, "owner-alex").status == terminal
        assert all(m.state == "withdrawn" for m in db.scalars(select(Message).where(Message.chat_id == "private-alex")))


def test_one_shot_context_task_first_and_second_input_end_intro_without_losing_work(product):
    onboarding.start(product, "owner-alex")
    first = send(product)["request"]
    context = tool_context(product, first["id"])
    assert context["onboarding"]["status"] == "active"
    prompt = conversation_system(context)
    assert "First meeting" in prompt and "handle it immediately" in prompt
    tools = RuntimeBridge.__new__(RuntimeBridge)._tools(context, SimpleNamespace(snapshot={}, resumable=False))
    assert onboarding.TOOL in {t.name for t in tools}
    second = send(product, body="Continue with the actual checklist")["request"]
    assert product.get_runtime_context(second["id"])["onboarding"] is None
    assert onboarding.get_state(product, "owner-alex")["status"] == "completed"
    assert product.get_request(first["id"])["status"] == "queued"
    assert product.get_request(second["id"])["status"] == "queued"
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(Message).where(Message.kind == "system")) == 1


@pytest.mark.parametrize("source,raw,requester,worker", [
    ("private", None, "owner-alex", True),
    ("scheduled_task", None, "owner-alex", False),
    ("subagent_result", None, "owner-alex", False),
    ("credential_result", None, "owner-alex", False),
    ("followup_result", None, "owner-alex", False),
    ("private", "Please write this message", "owner-alex", False),
    ("private", None, "owner-maya", False),
])
def test_temporary_role_boundaries_exclude_prompt_and_tool(product, source, raw, requester, worker):
    onboarding.start(product, "owner-alex")
    first = send(product)["request"]
    context = {**tool_context(product, first["id"]), "request_source": source,
               "raw_owner_input": raw, "request_requester_id": requester}
    assert "First meeting" not in conversation_system(context, worker=worker)
    session = SimpleNamespace(snapshot={}, resumable=False, subagent_creation={"task": "assigned"} if worker else None)
    tools = RuntimeBridge.__new__(RuntimeBridge)._tools(context, session)
    assert (onboarding.TOOL in {tool.name for tool in tools}) == worker


def test_current_request_scope_and_retry_generation_not_eligible(product):
    onboarding.start(product, "owner-alex")
    first = send(product)["request"]
    context = tool_context(product, first["id"])
    finished = asyncio.run(product.runtime_tool(onboarding.TOOL, {"status": "completed"}, {**context, "runtime_session_id": "worker-child"}))
    assert finished["status"] == "completed"
    with pytest.raises(HTTPException):
        asyncio.run(product.runtime_tool(onboarding.TOOL, {"status": "completed"}, {**context, "owner_id": "owner-maya"}))
    with product.transaction() as db:
        row = db.get(ReplyRequest, first["id"])
        row.generation = 1
    assert product.get_runtime_context(first["id"])["onboarding"] is None


def test_failure_rolls_back_welcome_and_disabling_has_no_effects(product, monkeypatch):
    original = product.append_message
    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Synthetic persistence failure")
    monkeypatch.setattr(product, "append_message", failed)
    with pytest.raises(RuntimeError):
        onboarding.start(product, "owner-alex")
    with product.transaction() as db:
        assert db.get(OnboardingState, "owner-alex") is None
        assert db.get(Chat, "private-alex").next_message_seq == 1
    monkeypatch.setattr(product, "append_message", original)
    monkeypatch.setattr(onboarding, "settings", replace(onboarding.settings, onboarding_enabled=False))
    assert onboarding.start(product, "owner-alex")["status"] == "disabled"
    assert onboarding.skip(product, "owner-alex")["status"] == "disabled"
    with product.transaction() as db:
        assert db.get(OnboardingState, "owner-alex") is None
        assert db.scalar(select(func.count()).select_from(Message)) == 0


def test_frozen_finish_declaration_survives_terminal_progress_or_disabled_feature(product, monkeypatch):
    onboarding.start(product, "owner-alex")
    first = send(product)["request"]
    context = tool_context(product, first["id"])
    bridge = RuntimeBridge.__new__(RuntimeBridge)
    fresh = next(t for t in bridge._tools(context, SimpleNamespace(snapshot={}, resumable=False)) if t.name == onboarding.TOOL)
    frozen = {"name": fresh.name, "description": "Saved first-meeting declaration", "parameters": fresh.parameters,
              "replay": fresh.replay, "version": fresh.version}
    onboarding.skip(product, "owner-alex")
    monkeypatch.setattr(onboarding, "settings", replace(onboarding.settings, onboarding_enabled=False))
    updated = {**context, "onboarding": None}
    tools = bridge._tools(updated, SimpleNamespace(snapshot={"tools": [frozen]}, resumable=True))
    assert len(tools) == 1 and tools[0].description == frozen["description"]
    assert tools[0].parameters == frozen["parameters"]
    assert asyncio.run(product.runtime_tool(onboarding.TOOL, {"status": "completed"}, updated))["status"] == "disabled"
    with product.transaction() as db:
        assert db.get(OnboardingState, "owner-alex").status == "skipped"


def test_owner_only_api_and_no_late_welcome_after_existing_history(product):
    async def run():
        owner = {"id": "owner-alex"}
        app = FastAPI()
        app.include_router(onboarding_api.router)
        app.dependency_overrides[identity] = lambda: owner["id"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://isolated") as client:
            assert (await client.get("/api/me/twin/onboarding")).json()["status"] == "new"
            result = (await client.post("/api/me/twin/onboarding/start")).json()
            assert result["chat_id"] == "private-alex" and result["welcome_message_id"]
            owner["id"] = "owner-maya"
            assert (await client.get("/api/me/twin/onboarding")).json()["status"] == "new"
            send(product, "maya")
            assert (await client.post("/api/me/twin/onboarding/start")).json()["status"] == "unavailable"
            owner["id"] = "owner-alex"
            assert (await client.post("/api/me/twin/onboarding/skip")).json()["status"] == "skipped"
            assert (await client.post("/api/me/twin/onboarding/start")).json()["status"] == "skipped"
    asyncio.run(run())
