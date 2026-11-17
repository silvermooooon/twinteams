"""Stored Activity titles with real SDK execution, PostgreSQL and HTTP reads."""
import asyncio
import json
import unicodedata
from time import perf_counter
from uuid import uuid4

import httpx
import pytest
from agent_runtime import (
    AssistantMessageEventStream,
    Models,
    ProviderConfig,
    RuntimeConfig,
    assistant_message,
)
from agent_runtime.sessions import DatabaseSession
from sqlalchemy import event, inspect, select, text
from test_api_success_paths import http_product as http_product  # noqa: PLC0414
from test_api_success_paths import product as product  # noqa: PLC0414
from test_context_acceptance import sql_reads
from test_early_reply import FINAL, OPENING
from test_early_reply import response as reply_response
from test_native_web_search import response

from app import activity_titles, backfill_activity
from app import service as service_module
from app.activity_titles import (
    DEFAULT_TITLE,
    MAX_INPUT_CHARS,
    MAX_TITLE_WIDTH,
    generate_title,
    normalize_title,
)
from app.models import ActivityProjection, ChangeOutbox
from app.runtime_bridge import RuntimeBridge

LONG_INPUT = "Please review the project delivery plan. " + "Check dependencies and explain the unresolved risks in detail. " * 30


def test_activity_and_opening_are_visible_while_title_is_pending(http_product, monkeypatch, record_property):
    product, client = http_product
    monkeypatch.setattr(activity_titles, "TITLE_TIMEOUT_SECONDS", 20)

    async def run():
        runtime = RuntimeBridge(product)
        title_started, release_title = asyncio.Event(), asyncio.Event()
        work_started, release_work = asyncio.Event(), asyncio.Event()
        actual_tool = product.runtime_tool
        calls = []

        async def work(name, args, context):
            assert name == "list_authorized_chats"
            work_started.set()
            await release_work.wait()
            return await actual_tool(name, args, context)

        monkeypatch.setattr(product, "runtime_tool", work)

        async def offline(model, context, options):
            if options.get("_purpose") == "activity_title":
                title_started.set()
                await release_title.wait()
                return reply_response(model, "Review delivery risks")
            calls.append(context)
            if len(calls) == 1:
                return reply_response(model, OPENING, "start_work")
            if len(calls) == 2:
                return reply_response(model, tool="list_authorized_chats")
            assert len(calls) == 3
            return reply_response(model, FINAL)

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        product.bind_runtime(runtime)
        execution = None
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(client.app), base_url="http://testserver", cookies=dict(client.cookies)) as browser:
                sent = await browser.post("/api/chats/private-alex/messages", json={"body": LONG_INPUT, "client_request_key": uuid4().hex})
                assert sent.status_code == 200
                request = sent.json()["request"]
                accepted = perf_counter()
                initial = await browser.get("/api/chats/private-alex")
                execution = asyncio.create_task(product.execute_request(request["id"]))
                await asyncio.wait_for(title_started.wait(), 3)
                page = await browser.get("/api/chats/private-alex/activity")
                elapsed = (perf_counter() - accepted) * 1000
                record_property("accept_to_activity_ms", round(elapsed, 3))
                assert page.status_code == 200 and elapsed < 3000
                item, = page.json()["items"]
                assert item["status"] == "running" and item["title"] == DEFAULT_TITLE
                assert not release_title.is_set() and not execution.done()
                visible = await browser.get("/api/chats/private-alex")
                assert visible.json()["revision"] > initial.json()["revision"]
                with product.transaction() as db:
                    assert db.scalar(select(ChangeOutbox.id).where(
                        ChangeOutbox.chat_id == "private-alex", ChangeOutbox.revision == visible.json()["revision"]))
                # Neither the opening, work tools nor completion may wait on a title.
                await asyncio.wait_for(work_started.wait(), 3)
                opening = await browser.get("/api/chats/private-alex")
                assert [row["body"] for row in opening.json()["messages"] if row["kind"] == "twin"] == [OPENING]
                assert product.get_request(request["id"])["status"] == "running"
                record_property("accept_to_opening_ms", round((perf_counter() - accepted) * 1000, 3))
                release_work.set()
                await asyncio.wait_for(asyncio.shield(execution), 3)
                assert product.get_request(request["id"])["status"] == "completed"
                assert product.activity_page("owner-alex", "private-alex")["items"][0]["title"] == DEFAULT_TITLE
                before_title = (await browser.get("/api/chats/private-alex")).json()["revision"]
                release_title.set()
                await asyncio.wait_for(asyncio.gather(*runtime.activity_title_tasks), 3)
                updated = (await browser.get("/api/chats/private-alex/activity")).json()["items"]
                assert len(updated) == 1 and updated[0]["run_id"] == item["run_id"]
                assert updated[0]["title"] == "Review delivery risks"
                assert updated[0]["status"] == "completed"
                detail = (await browser.get("/api/chats/private-alex")).json()
                assert detail["revision"] > before_title
                assert [row["body"] for row in detail["messages"] if row["kind"] == "twin"] == [OPENING, FINAL]
        finally:
            release_title.set()
            release_work.set()
            if execution:
                await execution
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("boundary", ["stop", "reset", "shutdown", "failure"])
def test_pending_title_obeys_stop_reset_shutdown_and_failure(product, monkeypatch, boundary, caplog):
    monkeypatch.setattr(activity_titles, "TITLE_TIMEOUT_SECONDS", 20)

    async def run():
        runtime = RuntimeBridge(product)
        title_started, release_title = asyncio.Event(), asyncio.Event()
        reply_started, release_reply = asyncio.Event(), asyncio.Event()
        title_signals = []

        async def offline(model, context, options):
            if options.get("_purpose") == "activity_title":
                title_signals.append(options["signal"])
                title_started.set()
                await release_title.wait()
                return reply_response(model, "Review delivery risks")
            reply_started.set()
            if boundary == "stop":
                await options["signal"].run(release_reply.wait())
            return reply_response(model, FINAL, error=boundary == "failure")

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        product.bind_runtime(runtime)
        request = product.send_message("owner-alex", "private-alex", LONG_INPUT, uuid4().hex)["request"]
        execution = asyncio.create_task(product.execute_request(request["id"]))
        closed = False
        try:
            await asyncio.wait_for(title_started.wait(), 3)
            await asyncio.wait_for(reply_started.wait(), 3)
            pending = tuple(runtime.activity_title_tasks)
            assert len(pending) == 1 and not pending[0].done()
            if boundary == "stop":
                await product.cancel_request("owner-alex", request["id"])
                await runtime.signal_stop(product.get_request(request["id"]))
            await asyncio.wait_for(asyncio.shield(execution), 3)
            if boundary == "reset":
                result = await product.db_call("personal_reset", "owner-alex", uuid4().hex, 0)
                assert result["generation"] == 1
            if boundary == "shutdown":
                await asyncio.wait_for(runtime.close(), 3)
                closed = True
            elif boundary != "stop":
                release_title.set()
            await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 3)
            assert title_signals[0].aborted
            assert not runtime.activity_title_tasks
            rows = product.activity_page("owner-alex", "private-alex")["items"]
            assert rows == [] if boundary == "reset" else len(rows) == 1 and rows[0]["title"] == DEFAULT_TITLE
        finally:
            release_reply.set()
            release_title.set()
            await execution
            if not closed:
                await runtime.close()

    asyncio.run(run())
    assert "Task exception was never retrieved" not in caplog.text


@pytest.mark.parametrize("owners", [["owner-maya"], ["owner-maya", "owner-jordan"]])
def test_human_pair_and_group_sends_have_no_activity(http_product, owners):
    product, client = http_product
    created = client.post("/api/chats", json={"owner_ids": owners})
    assert created.status_code == 200
    chat = created.json()
    assert chat["kind"] == "group"
    sent = client.post(f"/api/chats/{chat['id']}/messages", json={"body": "Human conversation", "client_request_key": uuid4().hex})
    assert sent.status_code == 200 and sent.json()["request"] is None
    assert client.get(f"/api/chats/{chat['id']}/activity").status_code == 403
    with product.transaction() as db:
        assert db.scalar(select(ActivityProjection).where(ActivityProjection.chat_id == chat["id"])) is None


@pytest.mark.parametrize("title,task,expected", [
    ("  Review delivery risks\n", LONG_INPUT, "Review delivery risks"),
    ("“梳理项目交付风险”", "请帮我详细检查这份项目计划，尤其注意其中的交付风险。" * 20, "梳理项目交付风险"),
    ("تقييم مخاطر التسليم", "أرجو فحص خطة المشروع ومراجعة جميع المخاطر المتعلقة بالتسليم", "تقييم مخاطر التسليم"),
    ("Analyser les de\u0301lais", "Vérifiez le calendrier et les dépendances", "Analyser les délais"),
    ("", LONG_INPUT, None),
    (" \n\t", LONG_INPUT, None),
    (LONG_INPUT, LONG_INPUT, None),
    (LONG_INPUT[:100] + "…", LONG_INPUT, None),
    ("Please review the project delivery plan and assess the risks", LONG_INPUT, None),
    ("Analyser le café", "Analyser le cafe\u0301", None),
], ids=["trim", "chinese", "arabic", "unicode", "empty", "whitespace", "echo", "clipped-echo", "clipped-prefix", "unicode-echo"])
def test_title_normalization(title, task, expected):
    assert normalize_title(title, task) == expected


@pytest.mark.parametrize("title", ["Review every dependency and prepare a detailed delivery risk assessment", "梳理项目交付风险并整理所有依赖事项以及负责人和日期"])
def test_overlong_generated_titles_are_bounded(title):
    result = normalize_title(title, LONG_INPUT)
    assert result and result.endswith("…")
    assert sum(2 if unicodedata.east_asian_width(c) in {"W", "F"} else 1 for c in result) <= MAX_TITLE_WIDTH


@pytest.mark.parametrize("owner_input,generated,expected", [
    (LONG_INPUT, "Review delivery risks", "Review delivery risks"),
    ("请审查这份项目计划，逐项分析依赖关系、负责人和交付风险。" * 30, "梳理项目交付风险", "梳理项目交付风险"),
    ("プロジェクトの計画について、依存関係と納期の問題を詳しく調べてください。" * 20, "納期リスクの確認", "納期リスクの確認"),
    (LONG_INPUT, " \n ", DEFAULT_TITLE),
    (LONG_INPUT, "failure", DEFAULT_TITLE),
    (LONG_INPUT, "incomplete", DEFAULT_TITLE),
    (LONG_INPUT, "provider-error", DEFAULT_TITLE),
    (LONG_INPUT, LONG_INPUT, DEFAULT_TITLE),
    ("请详细检查计划中的风险。" * 40, "failure", DEFAULT_TITLE),
    (LONG_INPUT, "Review every dependency and prepare a detailed delivery risk assessment", "Review every dependency and…"),
], ids=["english", "chinese", "japanese", "empty", "failure", "incomplete", "provider-error", "echo", "chinese-failure", "overlong"])
def test_generated_title_is_stored_once_and_detail_retains_input(http_product, caplog, owner_input, generated, expected):
    product, client = http_product

    async def run():
        runtime = RuntimeBridge(product)
        title_calls, reply_calls = [], []

        def offline(model, context, options):
            is_title = options.get("_purpose") == "activity_title"
            (title_calls if is_title else reply_calls).append(context)
            if is_title and generated == "failure":
                raise RuntimeError("Synthetic provider failure with private diagnostic text")
            body = generated if is_title else "The requested review is complete."
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model,
                content=[{"type": "text", "text": body}],
                stopReason="error" if is_title and generated == "provider-error" else "length" if is_title and generated == "incomplete" else "stop")})
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        product.bind_runtime(runtime)
        try:
            request_id = product.send_message("owner-alex", "private-alex", owner_input, uuid4().hex)["request"]["id"]
            await product.execute_request(request_id)
            await asyncio.gather(*runtime.activity_title_tasks)
            request = product.get_request(request_id)
            assert request["status"] == "completed"
            key = request["session_id"], request["run_id"]
            with product.transaction() as db:
                assert db.get(ActivityProjection, key).summary_title == expected
            async with httpx.AsyncClient(transport=httpx.ASGITransport(client.app), base_url="http://testserver", cookies=dict(client.cookies)) as browser:
                for _ in range(2):
                    page = await browser.get("/api/chats/private-alex/activity")
                    assert page.status_code == 200
                    item = next(row for row in page.json()["items"] if row["run_id"] == key[1])
                    assert item["title"] == expected and item["title"] != owner_input
                    assert (await browser.get("/api/bootstrap")).status_code == 200
                detail = await browser.get(f"/api/chats/private-alex/activity/{key[0]}/{key[1]}")
                assert detail.status_code == 200
                assert detail.json()["run"]["title"] == expected
                received = next(row for row in detail.json()["events"] if row["type"] == "run_started")
                assert owner_input.strip() in received["input"]
            # Terminal replay and status updates must not overwrite or regenerate the title.
            await runtime.execute(request, product.get_runtime_context(request_id, include_completed=True))
            assert product.activity_page("owner-alex", "private-alex")["items"][0]["title"] == expected
            assert len(title_calls) == len(reply_calls) == 1
            assert not title_calls[0].tools
            assert len(title_calls[0].messages[-1]["content"][0]["text"]) <= MAX_INPUT_CHARS
        finally:
            await runtime.close()
    asyncio.run(run())
    assert "private diagnostic text" not in caplog.text


def test_child_title_survives_later_events_and_historical_import(product, monkeypatch):
    async def run():
        runtime = RuntimeBridge(product)
        title_calls = []
        child_title_started, release_title = asyncio.Event(), asyncio.Event()

        async def offline(model, context, options):
            is_title = options.get("_purpose") == "activity_title"
            if is_title:
                title_calls.append(context)
                if len(title_calls) == 2:
                    child_title_started.set()
                    await release_title.wait()
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model,
                content=[{"type": "text", "text": "Audit dependencies" if is_title else "Reviewed the dependencies."}], stopReason="stop")})
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        product.bind_runtime(runtime)
        try:
            request_id = product.send_message("owner-alex", "private-alex", LONG_INPUT, uuid4().hex)["request"]["id"]
            await product.execute_request(request_id)
            root = product.get_request(request_id)
            child = DatabaseSession(runtime.store, session_id=uuid4().hex, tenant_id=runtime.tenant_id, user_id="owner-alex")
            await child.commit("subagent_created", parent_session_id=root["session_id"], parent_run_id=root["run_id"],
                root_run_id=root["session_id"] + "/" + root["run_id"], name="worker", task=LONG_INPUT, operation_id=uuid4().hex)
            agent = await runtime._child_factory(child)
            execution = asyncio.create_task(agent.prompt(LONG_INPUT))
            await asyncio.wait_for(child_title_started.wait(), 3)
            await asyncio.wait_for(asyncio.shield(execution), 3)
            pending = next(row for row in product.activity_page("owner-alex", "private-alex")["items"] if row["source"] == "subagent")
            assert pending["title"] == "Background task" and pending["status"] == "completed"
            release_title.set()
            await asyncio.gather(*runtime.activity_title_tasks)
            assert agent.state.error_message is None
            rows = product.activity_page("owner-alex", "private-alex")["items"]
            assert len(rows) == 2 and all(row["title"] == "Audit dependencies" for row in rows)
            assert len(title_calls) == 2
            assert next(row for row in rows if row["source"] == "subagent")["parent_run_id"] == root["run_id"]
            # The explicit historical importer must not reintroduce raw-input list titles.
            with product.transaction() as db:
                db.delete(db.get(ActivityProjection, (root["session_id"], root["run_id"])))
            monkeypatch.setattr(backfill_activity, "engine", service_module.engine)
            backfill_activity.backfill_activity()
            imported = next(row for row in product.activity_page("owner-alex", "private-alex")["items"] if row["run_id"] == root["run_id"])
            assert imported["title"] == DEFAULT_TITLE
            assert len(title_calls) == 2
        finally:
            release_title.set()
            await runtime.close()
    asyncio.run(run())


def test_list_and_bootstrap_reads_are_batched_and_read_only(http_product):
    product, client = http_product
    counts = []
    for size in (1, 100):
        with product.transaction() as db:
            for index in range(0 if size == 1 else 1, size):
                db.add(ActivityProjection(session_id=f"title-{index}", run_id=f"title-{index}",
                    chat_id="private-alex", owner_id="owner-alex", title=LONG_INPUT,
                    summary_title=f"Review item {index}"))
            if size == 100:
                db.add(ActivityProjection(session_id="hidden-title", run_id="hidden-title", chat_id="private-alex", owner_id="owner-alex", user_visible=False, title=LONG_INPUT))
        statements = []
        def record(_connection, _cursor, statement, *_args, statements=statements):
            statements.append(" ".join(statement.lower().split()))
        event.listen(service_module.engine, "before_cursor_execute", record)
        try:
            with sql_reads() as reads:
                page = client.get("/api/chats/private-alex/activity", params={"limit": 100})
            assert page.status_code == 200 and len(page.json()["items"]) == size
            assert all(row["title"].startswith("Review item") for row in page.json()["items"])
            projections = [row for row in reads if " from tt_activity_runs" in row["sql"]]
            assert len(projections) == 1 and projections[0]["rows"] == size
            assert "tt_activity_runs.user_visible" in projections[0]["sql"].split(" where ")[1]
            assert " limit " in projections[0]["sql"]
            counts.append(len(reads))
            with sql_reads() as bootstrap_reads:
                assert client.get("/api/bootstrap").status_code == 200
            assert not any("tt_activity_runs" in row["sql"] or "agent_events" in row["sql"] for row in bootstrap_reads)
            assert not any(sql.startswith(("insert", "update", "delete")) for sql in statements)
            assert not any("agent_" in row["sql"] for row in reads)
        finally:
            event.remove(service_module.engine, "before_cursor_execute", record)
    assert counts[0] == counts[1]


def test_additive_upgrade_preserves_legacy_input_and_never_rewrites_history(product):
    with product.transaction() as db:
        db.add(ActivityProjection(session_id="legacy", run_id="legacy", chat_id="private-alex", owner_id="owner-alex", title=LONG_INPUT))
    with service_module.engine.begin() as connection:
        connection.execute(text("ALTER TABLE tt_activity_runs DROP COLUMN summary_title"))
    with pytest.raises(RuntimeError, match="Activity summary titles"):
        product.check_initialized()
    activity_titles.upgrade_activity_title_schema(service_module.engine)
    activity_titles.check_activity_title_schema(inspect(service_module.engine))
    with product.transaction() as db:
        row = db.get(ActivityProjection, ("legacy", "legacy"))
        assert row.title == LONG_INPUT and row.summary_title == DEFAULT_TITLE
        row.summary_title = "Review delivery risks"
    activity_titles.upgrade_activity_title_schema(service_module.engine)
    assert product.activity_page("owner-alex", "private-alex")["items"][0]["title"] == "Review delivery risks"


def test_actual_responses_payload_is_tool_free_bounded_and_uses_configured_model():
    async def run():
        payloads = []
        def handler(request):
            payloads.append(json.loads(request.content))
            return response("openai-responses", body="Review delivery risks", searched=False)
        config = RuntimeConfig(provider="openai", model="gpt-6-luna", api="openai-responses", parameters={"reasoning": "high", "max_tokens": 4096},
            providers={"openai": ProviderConfig("openai", "openai-responses", "https://provider.invalid/v1", "offline")})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await generate_title(Models(config=config, client=client), LONG_INPUT * 10) == "Review delivery risks"
        assert len(payloads) == 1
        payload = payloads[0]
        assert payload["model"] == "gpt-6-luna" and payload["max_output_tokens"] == 128
        assert payload["reasoning"]["effort"] == "none" and "tools" not in payload
        assert len(json.dumps(payload, ensure_ascii=False)) < MAX_INPUT_CHARS + 1500
    asyncio.run(run())


def test_empty_input_skips_generation_and_timeout_aborts_provider(monkeypatch):
    monkeypatch.setattr(activity_titles, "TITLE_TIMEOUT_SECONDS", .01)
    async def run():
        models = Models(config=RuntimeConfig.from_env())
        calls = []
        async def stalled(_model, _context, options):
            calls.append(options["signal"])
            await asyncio.Event().wait()
        models.register_api("offline-regression", stalled)
        assert await generate_title(models, " \n ") is None and not calls
        assert await generate_title(models, LONG_INPUT) is None
        assert len(calls) == 1 and calls[0].aborted
    asyncio.run(run())
