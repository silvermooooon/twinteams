"""Authenticated successful HTTP paths for the optional API budget inventory.

The existing domain fixtures create real PostgreSQL records and local secret
files. No worker or live model runs; mutations must persist/enqueue and return.
"""
import asyncio
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_credentials import pending as pending_credential
from test_credentials import product as product  # noqa: PLC0414
from test_questions import pending as pending_question

from app import api, credential_api, personal, personal_api, question_api, scheduled_task_api


@pytest.fixture
def http_product(product, monkeypatch):
    application = FastAPI()
    for module in (api, credential_api, personal_api, question_api, scheduled_task_api):
        monkeypatch.setattr(module, "service", product)
        application.include_router(module.router)
    monkeypatch.setattr(personal, "service", product)
    with TestClient(application) as client:
        assert client.post("/api/auth/login", json={"username": "DEV-AAA"}).status_code == 200
        yield product, client


def test_account_settings_tracking_and_reminder_http_lifecycle(http_product):
    _, client = http_product
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/people").json()
    assert client.get("/api/settings").status_code == 200
    assert client.patch("/api/settings", json={"default_reply_prompt": "Keep updates concise."}).status_code == 200
    path = "/api/chats/private-alex"
    assert client.patch(path + "/settings", json={"sidebar_open": True, "sidebar_tab": "tracking"}).status_code == 200
    sent = client.post(path + "/messages", json={"body": "Deliver the synthetic report next week.", "client_request_key": uuid4().hex}).json()
    source = sent["message"]["id"]
    manual = client.post(path + "/reply-requests", json={"message_id": source, "target_twin_id": "twin-alex", "client_request_key": uuid4().hex})
    assert manual.status_code == 200
    cancelled = client.post("/api/reply-requests/" + manual.json()["id"] + "/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    created = client.post(path + "/tracking", json={"title": "Synthetic delivery", "description": "Prepare review.", "source_message_ids": [source]})
    assert created.status_code == 200
    topic_path = path + "/tracking/" + created.json()["id"]
    assert client.patch(topic_path, json={"description": "Review accepted."}).status_code == 200
    assert client.post(topic_path + "/updates", json={"body": "Owner reviewed scope.", "source_message_ids": [source]}).status_code == 200
    assert client.delete(topic_path).status_code == 200
    reminder = client.post("/api/reminders", json={"title": "Review delivery", "chat_id": "private-alex"})
    assert reminder.status_code == 200
    assert client.patch("/api/reminders/" + reminder.json()["id"], json={"done": True}).json()["done"]
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_workspace_skill_and_memory_controls_use_real_local_files(http_product):
    product, client = http_product
    workspace = "/api/chats/private-alex/workspace"
    uploaded = client.post(workspace + "/files?name=review.md", content=b"# Synthetic review\n")
    assert uploaded.status_code == 200
    assert client.get(workspace).status_code == 200
    path = workspace + "/files/" + uploaded.json()["id"]
    assert client.get(path).content == b"# Synthetic review\n"
    assert client.get(path + "/preview").text == "# Synthetic review\n"
    name = "synthetic-" + uuid4().hex
    skill = personal.resolve_twin_workspace("owner-alex") / "skills" / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\ndescription: Synthetic report\n---\nWrite the report.")
    detail = client.get("/api/me/twin/skills/detail", params={"name": name})
    assert detail.status_code == 200 and name in detail.text
    product.send_message("owner-alex", "private-alex", "Remember this synthetic project.", uuid4().hex)
    dream = client.post("/api/me/twin/memory/dream")
    assert dream.status_code == 200 and dream.json()["request_id"]
    status = client.get("/api/me/twin/reset")
    assert status.status_code == 200
    reset = client.post("/api/me/twin/reset", json={"idempotency_key": uuid4().hex, "expected_generation": status.json()["generation"]})
    assert reset.status_code in {200, 202}
    assert reset.json()["generation"] == status.json()["generation"] + 1


def test_presence_success_uses_namespaced_real_redis(http_product, monkeypatch):
    from redis.asyncio import Redis

    from app import service as service_module
    from app.notifications import notifications
    from checks.local_stack import environment

    _, client = http_product
    prefix = "api-budget-presence-" + uuid4().hex
    monkeypatch.setattr(service_module, "settings", replace(service_module.settings, redis_prefix=prefix))

    async def run():
        redis = Redis.from_url(environment("api")["TWINTEAMS_REDIS_URL"], decode_responses=True)
        monkeypatch.setattr(notifications, "redis", redis)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(client.app), base_url="http://testserver", cookies=dict(client.cookies)) as browser:
                response = await browser.post("/api/presence", json={"device_id": "budget-device", "focused_chat_id": "private-alex", "visible": True})
                assert response.status_code == 200 and response.json()["ok"]
            assert await redis.get(prefix + ":presence:owner-alex:budget-device")
        finally:
            await redis.delete(prefix + ":presence:owner-alex:budget-device", prefix + ":devices:owner-alex")
            await redis.aclose()

    asyncio.run(run())


def test_pending_duet_send_can_be_cancelled_and_channel_closed(http_product):
    from duet_test_support import approved_chat
    from test_duet_message_delivery import origin, send

    product, client = http_product
    chat = approved_chat(product, "duet-budget")
    _, context = origin(product)
    sends = [send(product, context, chat, f"Synthetic peer update {index}") for index in range(7)]
    pending = sends[-1]["message_id"]
    assert client.delete(f"/api/duet-conversations/{chat}/pending-messages/{pending}").status_code == 200
    response = client.post(f"/api/duet-conversations/{chat}/close")
    assert response.status_code == 200


def test_built_frontend_fallback_delivers_html():
    from app import main

    if not main.frontend_dist.is_dir():
        pytest.skip("The optional frontend route is mounted only after npm run build")
    client = TestClient(main.app, follow_redirects=False)
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    client.close()


def test_schedule_lifecycle_returns_persisted_results(http_product):
    product, client = http_product
    path = "/api/me/twin/scheduled-tasks"
    response = client.post(path, json={"title": "Review synthetic delivery", "instructions": "Review recent updates.",
        "timezone": "UTC", "schedule": {"kind": "interval", "seconds": 3600}})
    assert response.status_code == 200
    task = response.json()
    assert any(item["id"] == task["id"] for item in client.get(path).json()["items"])
    assert client.patch(path + "/" + task["id"], json={"title": "Updated review"}).json()["title"] == "Updated review"
    run = client.post(path + "/" + task["id"] + "/run-now", json={"idempotency_key": uuid4().hex})
    assert run.status_code == 200
    assert product.get_request(run.json()["request_id"])["status"] == "queued"
    history = client.get(path + "/" + task["id"] + "/history")
    assert history.status_code == 200 and history.json()["items"]
    product.complete_request(run.json()["request_id"])
    assert client.delete(path + "/" + task["id"]).status_code == 200
    assert not any(item["id"] == task["id"] for item in client.get(path).json()["items"])


def test_credential_and_question_controls_complete_or_dismiss_without_model(http_product):
    product, client = http_product
    _, _, credential = pending_credential(product, name="budget-token")
    assert client.get("/api/me/twin/credential-requests").json()["requests"]
    completed = client.post(f"/api/me/twin/credential-requests/{credential['id']}/complete",
        json={"values": {"FAKE_ACCESS_TOKEN": "offline-synthetic-value"}})
    assert completed.status_code == 200 and completed.json()["status"] == "fulfilled"
    assert client.delete("/api/me/twin/credentials/budget-token").status_code == 200
    _, _, another = pending_credential(product, name="budget-dismiss")
    dismissed = client.post(f"/api/me/twin/credential-requests/{another['id']}/dismiss")
    assert dismissed.status_code == 200 and dismissed.json()["status"] == "dismissed"
    request, _, question, _ = pending_question(product)
    product.complete_request(request["id"])
    response = client.post(f"/api/questions/{question['id']}/dismiss")
    assert response.status_code == 200 and response.json()["status"] == "cancelled"


def test_local_provider_save_is_offline_and_uses_only_temporary_files(product, tmp_path, monkeypatch):
    from app import provider_config

    (tmp_path / "data").mkdir()
    monkeypatch.setattr(provider_config, "ROOT", tmp_path)
    monkeypatch.setenv("TWINTEAMS_LOCAL_PROVIDER_CONFIG", "true")
    monkeypatch.setattr(provider_config, "SECRET_DIRECTORY", tmp_path / "data" / "provider-secrets")
    monkeypatch.setattr(provider_config, "SECRET_PATH", tmp_path / "data" / "provider-secrets" / "openai.env")
    monkeypatch.setattr(api, "service", product)
    application = FastAPI()
    application.include_router(api.router)
    application.include_router(provider_config.router)
    with TestClient(application, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        assert client.post("/api/auth/login", json={"username": "DEV-AAA"}).status_code == 200
        assert client.get("/api/local-provider-config").status_code == 200
        assert client.get("/api/local-provider-config/save-page").status_code == 200
        saved = client.post("/api/local-provider-config/save",
            headers={"origin": "http://127.0.0.1", "x-twinteams-config": "1"},
            json={"api_key": "sk-offline-synthetic-only", "confirm_local_save": True})
        assert saved.status_code == 200 and saved.json()["saved"] and not saved.json()["enabled"]
        assert provider_config.SECRET_PATH.is_file()


def test_message_center_receipt_is_durable_without_waiting_for_duet(product, monkeypatch):
    from app import message_center
    from app import service as service_module

    monkeypatch.setattr(service_module, "service", product)
    monkeypatch.setattr(message_center, "settings", replace(message_center.settings,
        message_center_url="http://offline-center.invalid", message_center_token="offline-fixture-token"))
    application = FastAPI()
    application.include_router(message_center.router)
    with TestClient(application) as client:
        response = client.post("/api/message-center/deliveries", headers={"x-message-center-token": "offline-fixture-token"},
            json={"delivery_id": "budget-receipt", "message_id": "budget-announcement", "version": 1,
                  "owner_id": "owner-alex", "mode": "immediate", "message": {"title": "Synthetic announcement", "body": "Review this update."}})
        assert response.status_code == 200
        assert product.get_request(response.json()["request_id"])["status"] == "queued"


def test_activity_reads_real_sdk_postgresql_journal_over_http(http_product):
    from agent_runtime import AssistantMessageEventStream, assistant_message

    from app.runtime_bridge import RuntimeBridge

    product, client = http_product

    async def run():
        runtime = RuntimeBridge(product)

        def offline(model, context, options):
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model,
                content=[{"type": "text", "text": "Synthetic journal result."}], stopReason="stop")})
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        product.bind_runtime(runtime)
        try:
            sent = product.send_message("owner-alex", "private-alex", "Review the synthetic result.", uuid4().hex)
            await product.execute_request(sent["request"]["id"])
            request = product.get_request(sent["request"]["id"])
            assert request["status"] == "completed"
            path = f"/api/chats/private-alex/activity/{request['session_id']}/{request['run_id']}"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(client.app), base_url="http://testserver", cookies=dict(client.cookies)) as browser:
                response = await browser.get(path)
                assert response.status_code == 200
                data = response.json()
                assert data["run"]["run_id"] == request["run_id"]
                assert any(event["type"] == "model_completed" and event["output"] == "Synthetic journal result." for event in data["events"])
                assert any(event["type"] == "run_completed" for event in data["events"])
                page = await browser.get(path, params={"limit": 2})
                assert page.status_code == 200 and page.json()["has_more"]
                later = await browser.get(path, params={"after_seq": page.json()["next_seq"]})
                assert later.status_code == 200
                assert all(event["seq"] > page.json()["next_seq"] for event in later.json()["events"])
        finally:
            await runtime.close()

    asyncio.run(run())
