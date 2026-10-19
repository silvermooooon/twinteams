"""Offline credentials: disposable PG, fake values, and real short-lived local shells."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agent_runtime.execution import create_execution_tools
from agent_runtime.sessions import SessionInfo
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import credentials
from app import service as service_module
from app.models import ReplyRequest
from app.twin_execution import TwinExecutionBackend, owner_execution_allowed
from checks.postgres import TestDatabase


def key():
    return uuid4().hex


@pytest.fixture
def product(tmp_path, monkeypatch):
    database = TestDatabase()
    engine = create_engine(database.url.replace("postgresql://", "postgresql+psycopg://"))
    monkeypatch.setattr(service_module, "engine", engine)
    monkeypatch.setattr(service_module, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(service_module, "settings", replace(service_module.settings, database_url=database.url))
    monkeypatch.setenv("AGENT_SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("AGENT_SECRETS_DIR", str(tmp_path / "host-secrets"))
    monkeypatch.setenv("AGENT_DB_URL", database.url)
    product = service_module.ProductService()
    product.initialize(create_schema=True)
    yield product
    product.db_pool.shutdown()
    engine.dispose()
    database.close()


def pending(product, *, name="test-api"):
    request = product.send_message("owner-alex", "private-alex", "offline credential test", key())["request"]
    context = product.get_runtime_context(request["id"])
    context["operation_id"] = key()
    result = credentials.create_request(product,
        {"name": name, "keynames": ["FAKE_ACCESS_TOKEN"], "description": "offline test", "reason": "continue test"}, context)
    return request, context, result


def test_pending_is_durable_and_does_not_hold_worker(product):
    _, context, row = pending(product)
    repeated = credentials.create_request(product,
        {"name": row["ref"], "keynames": ["FAKE_ACCESS_TOKEN"], "description": "offline test", "reason": "continue test"}, context)
    assert repeated["id"] == row["id"]
    assert row["status"] == "pending"
    assert product.tasks == {}
    # A fresh service reads the pending metadata; no runtime wait/lease exists.
    restarted = service_module.ProductService()
    try:
        assert row["id"] in [r["id"] for r in credentials.list_requests(restarted, "owner-alex")["requests"]]
        assert credentials.list_requests(restarted, "owner-maya")["requests"] == []
    finally:
        restarted.db_pool.shutdown()


def test_complete_is_once_and_builds_a_new_request(product):
    request, context, row = pending(product)
    result = credentials.complete(product, "owner-alex", row["id"], values={"FAKE_ACCESS_TOKEN": "fake-test-one"})
    again = credentials.complete(product, "owner-alex", row["id"], values={"FAKE_ACCESS_TOKEN": "should-not-overwrite"})
    assert result["continuation_request_id"] == again["continuation_request_id"]
    with product.transaction() as db:
        continuation = db.get(ReplyRequest, result["continuation_request_id"])
        assert continuation.id != request["id"]
        assert continuation.source == "credential_result"
        assert continuation.execution_config is None
        assert "not approve" in continuation.input_body
        assert db.scalar(select(func.count()).select_from(ReplyRequest).where(ReplyRequest.source == "credential_result")) == 1
    assert asyncio.run(credentials.owner_store("owner-alex").resolve(row["ref"])) == {"FAKE_ACCESS_TOKEN": "fake-test-one"}
    assert owner_execution_allowed(product, "owner-alex", result["continuation_request_id"])
    replayed = credentials.create_request(product,
        {"name": row["ref"], "keynames": ["FAKE_ACCESS_TOKEN"], "description": "offline test", "reason": "continue test"}, context)
    assert replayed == row  # Safe replay preserves its original pending result.


def test_saved_file_pg_failure_stays_pending_until_owner_chooses_existing(product, monkeypatch):
    _, _, row = pending(product)
    real_accept = product.accept_request
    def interrupted(*args, **kwargs):
        raise RuntimeError("simulated database interruption")
    monkeypatch.setattr(product, "accept_request", interrupted)
    with pytest.raises(RuntimeError, match="simulated"):
        credentials.complete(product, "owner-alex", row["id"], values={"FAKE_ACCESS_TOKEN": "fake-saved-before-pg"})
    assert credentials.list_requests(product, "owner-alex")["requests"][0]["status"] == "pending"
    assert asyncio.run(credentials.owner_store("owner-alex").describe(row["ref"])).ref == row["ref"]
    monkeypatch.setattr(product, "accept_request", real_accept)
    result = credentials.complete(product, "owner-alex", row["id"], existing_name=row["ref"])
    assert result["status"] == "fulfilled"


def test_cancel_or_foreign_owner_cannot_continue(product):
    request, _, row = pending(product)
    with pytest.raises(HTTPException) as error:
        credentials.complete(product, "owner-maya", row["id"], values={"FAKE_ACCESS_TOKEN": "fake-wrong-owner"})
    assert error.value.status_code == 404
    asyncio.run(product.cancel_request("owner-alex", request["id"]))
    with pytest.raises(HTTPException):
        credentials.complete(product, "owner-alex", row["id"], values={"FAKE_ACCESS_TOKEN": "fake-stopped"})
    assert credentials.list_requests(product, "owner-alex")["requests"][0]["status"] == "cancelled"
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReplyRequest).where(ReplyRequest.source == "credential_result")) == 0


def test_cancelling_origin_also_cancels_already_queued_continuation(product):
    request, _, row = pending(product)
    completed = credentials.complete(product, "owner-alex", row["id"], values={"FAKE_ACCESS_TOKEN": "fake-completed-before-cancel"})
    asyncio.run(product.cancel_request("owner-alex", request["id"]))
    with product.transaction() as db:
        continuation = db.get(ReplyRequest, completed["continuation_request_id"])
        assert continuation.cancel_requested and continuation.status == "cancelled"
    with pytest.raises(HTTPException):
        owner_execution_allowed(product, "owner-alex", completed["continuation_request_id"])


def test_group_credential_form_belongs_to_target_owner_and_keeps_requester(product):
    source = product.send_message("owner-maya", "group-alex-maya", "Synthetic request", key())["message"]
    request = product.manual_request("owner-maya", "group-alex-maya", source["id"], "twin-alex", key())
    context = product.get_runtime_context(request["id"])
    assert owner_execution_allowed(product, "owner-alex", request["id"])
    assert not owner_execution_allowed(product, "owner-maya", request["id"])
    row = credentials.create_request(product, {"name": "group-test", "keynames": ["TOKEN"]},
        {**context, "operation_id": key()})
    assert row["id"] in [r["id"] for r in credentials.list_requests(product, "owner-alex")["requests"]]
    assert credentials.list_requests(product, "owner-maya")["requests"] == []
    with pytest.raises(HTTPException):
        credentials.complete(product, "owner-maya", row["id"], values={"TOKEN": "fake-wrong-owner"})
    result = credentials.complete(product, "owner-alex", row["id"], values={"TOKEN": "fake-group-token"})
    with product.transaction() as db:
        continuation = db.get(ReplyRequest, result["continuation_request_id"])
        assert continuation.chat_id == "group-alex-maya" and continuation.target_twin_id == "twin-alex"
        assert continuation.requester_id == "owner-maya" and continuation.source_owner_id == "owner-maya"
        assert continuation.source_message_id == source["id"]
        assert continuation.execution_config is None  # fresh credential metadata, not action consent
    assert owner_execution_allowed(product, "owner-alex", result["continuation_request_id"])


def test_shell_uses_frozen_metadata_live_values_and_does_not_reuse_environment(product, tmp_path):
    async def run():
        identity = SessionInfo("offline-shell", "local", "owner-alex")
        store = credentials.make_secret_store(identity)
        await store.put("test", {"FAKE_ACCESS_TOKEN": "fake-shell-first"}, description="frozen description")
        catalog = [(await store.describe("test")).to_dict()]
        frozen = credentials.FrozenSecretStore(store, catalog)
        backend = TwinExecutionBackend(identity, tmp_path / "private-workspace", directory=tmp_path / "host-execution")
        tools = await create_execution_tools(session=SimpleNamespace(info=identity), backend=backend,
            secrets=frozen, credential_refs=["test"])
        bash = next(tool for tool in tools if tool.name == "bash")
        first = await bash.execute("one", {"command": "printf '%s' \"$FAKE_ACCESS_TOKEN\"", "credentials": ["test"]})
        assert "fake-shell-first" not in str(first)
        assert "[REDACTED]" in str(first)
        second = await bash.execute("two", {"command": "printf '%s' \"${FAKE_ACCESS_TOKEN-unset}\""})
        assert "unset" in str(second)
        await store.put("test", {"FAKE_ACCESS_TOKEN": "fake-shell-rotated"}, description="new description")
        assert (await frozen.describe("test")).description == "frozen description"
        rotated = await bash.execute("three", {"command": "test \"$FAKE_ACCESS_TOKEN\" = fake-shell-rotated && printf rotation-observed", "credentials": ["test"]})
        assert "rotation-observed" in str(rotated)
        assert bash.replay == "never"
        await store.put("test", {"CHANGED_BINDING": "fake-changed"})
        recovered_tools = await create_execution_tools(session=SimpleNamespace(info=identity), backend=backend,
            secrets=credentials.FrozenSecretStore(store, catalog), credential_refs=["test"])
        recovered_bash = next(tool for tool in recovered_tools if tool.name == "bash")
        assert "FAKE_ACCESS_TOKEN" in recovered_bash.description
        assert "CHANGED_BINDING" not in recovered_bash.description
        with pytest.raises(PermissionError, match="bindings changed"):
            await recovered_bash.execute("four", {"command": "printf should-not-run", "credentials": ["test"]})
        await store.delete("test")
        with pytest.raises(Exception, match="unavailable"):
            await bash.execute("five", {"command": "printf should-not-run", "credentials": ["test"]})
        assert backend.cwd == str((tmp_path / "private-workspace").resolve())
    asyncio.run(run())


def test_credential_http_validation_never_echoes_value(product, monkeypatch, caplog):
    from app.api import identity
    from app.credential_api import router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[identity] = lambda: "owner-alex"
    with TestClient(app) as client:
        invalid = client.put("/api/me/twin/credentials/http-test", json={"values": {"TOKEN": {"sensitive": "fake-do-not-echo"}}})
        assert invalid.status_code == 422
        assert "fake-do-not-echo" not in invalid.text
        assert "input" not in invalid.text
        saved = client.put("/api/me/twin/credentials/http-test", json={"values": {"TOKEN": "fake-http-form-value"}})
        assert saved.status_code == 200
        assert saved.json() == {"name": "http-test", "ref": "http-test", "description": "", "keynames": ["TOKEN"]}
        assert "fake-http-form-value" not in saved.text
        listing = client.get("/api/me/twin/credentials")
        assert listing.status_code == 200
        assert "fake-http-form-value" not in listing.text
        assert listing.headers["cache-control"] == "no-store"
        def bad_host_write(*args, **kwargs):
            raise RuntimeError("fake-sensitive-exception-value")
        monkeypatch.setattr(credentials, "manage", bad_host_write)
        failed = client.put("/api/me/twin/credentials/http-test", json={"values": {"TOKEN": "fake-sensitive-exception-value"}})
        assert failed.status_code == 500
        assert "fake-sensitive-exception-value" not in failed.text
        assert "fake-sensitive-exception-value" not in caplog.text


def test_unicode_credential_names_are_metadata(product):
    result = credentials.manage(product, "owner-alex", "  测试服务  ", values={"SERVICE_TOKEN": "fake-unicode-test"})
    assert result["name"] == "测试服务"
    assert result["ref"] == "测试服务"
    assert result["keynames"] == ["SERVICE_TOKEN"]
    assert "fake-unicode-test" not in str(result)


def test_assembly_freezes_catalog_child_has_no_memory_and_group_has_owner_tools(product, tmp_path, monkeypatch):
    from app import personal, twin_execution
    from app.models import Chat
    original_backend = twin_execution.TwinExecutionBackend
    monkeypatch.setattr(twin_execution, "TwinExecutionBackend", lambda identity, workspace:
        original_backend(identity, workspace, directory=tmp_path / "execution-state"))
    request = product.send_message("owner-alex", "private-alex", "offline assemble", key())["request"]
    context = product.get_runtime_context(request["id"])
    skill_root = personal.resolve_twin_workspace("owner-alex") / "skills" / "offline-demo"
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "SKILL.md").write_text("---\nname: offline-demo\ndescription: Original skill description\n---\nOffline instructions.\n")
    with product.transaction() as db:
        product.twin(db, "owner-alex").enabled_skill_names = ["offline-demo"]
    async def run():
        session = SimpleNamespace(info=SessionInfo("assembly-main", "local", "owner-alex"), subagent_creation=None)
        store = credentials.make_secret_store(session.info)
        await store.put("assembly-ref", {"ASSEMBLY_TOKEN": "fake-assembly-first"}, description="Original credential metadata")
        main_tools, assembled, _ = await twin_execution.assemble(product, session, context)
        main_names = {tool.name for tool in main_tools}
        assert "bash" in main_names and "load_skill" in main_names and "memory_write" in main_names
        initial = assembled["execution_config"]
        assert initial["credentials"][0]["description"] == "Original credential metadata"
        assert "fake-assembly-first" not in str(initial)
        (skill_root / "SKILL.md").write_text("---\nname: offline-demo\ndescription: New skill description\n---\nNew instructions.\n")
        await store.put("assembly-ref", {"ASSEMBLY_TOKEN": "fake-assembly-new"}, description="New credential metadata")
        child = SimpleNamespace(info=SessionInfo("assembly-child", "local", "owner-alex"), subagent_creation={"task": "offline worker"})
        child_tools, inherited, _ = await twin_execution.assemble(product, child, context, include_memory=False)
        assert inherited["execution_config"] == initial
        assert not any(tool.name.startswith("memory_") for tool in child_tools)
        recovered_tools, recovered, _ = await twin_execution.assemble(product, session, context)
        assert recovered["execution_config"] == initial
        recovered_bash = next(tool for tool in recovered_tools if tool.name == "bash")
        assert "Original credential metadata" in recovered_bash.description
        assert "New credential metadata" not in recovered_bash.description
        output = await recovered_bash.execute("rotation", {"command": "test \"$ASSEMBLY_TOKEN\" = fake-assembly-new && printf updated", "credentials": ["assembly-ref"]})
        assert "updated" in str(output)
        with product.transaction() as db:
            group = db.scalar(select(Chat).where(Chat.kind == "group"))
            group_request = product.accept_request(db, group, product.twin(db, "owner-alex").id, "owner-alex", "manual", key())
            group_id = group_request.id
        group_context = product.get_runtime_context(group_id)
        group_tools, group_config, _ = await twin_execution.assemble(product, session, group_context)
        assert group_config["execution_enabled"]
        assert {"bash", "memory_write", "load_skill"} <= {tool.name for tool in group_tools}
        from app.runtime_bridge import RuntimeBridge
        assert "request_credential" in {tool.name for tool in RuntimeBridge(product)._tools(group_config, session)}
    asyncio.run(run())


def test_legacy_unfinished_run_keeps_original_tools_and_new_prompt_gets_execution(product, tmp_path, monkeypatch):
    from app import personal, twin_execution
    original_backend = twin_execution.TwinExecutionBackend
    monkeypatch.setattr(twin_execution, "TwinExecutionBackend", lambda identity, workspace:
        original_backend(identity, workspace, directory=tmp_path / "execution-state"))
    request = product.send_message("owner-alex", "private-alex", "legacy offline", key())["request"]
    context = product.get_runtime_context(request["id"])
    async def run():
        session = SimpleNamespace(info=SessionInfo("legacy-main", "local", "owner-alex"),
            subagent_creation=None, resumable=True)
        tools, legacy, _ = await twin_execution.assemble(product, session, context)
        assert legacy["execution_config"]["version"] == 0
        assert not legacy["execution_enabled"]
        assert {tool.name for tool in tools} == {"memory_read", "memory_search", "memory_write", "memory_delete"}
        assert personal.get_execution_config(request["id"]) is None
        child = SimpleNamespace(info=SessionInfo("legacy-child", "local", "owner-alex"),
            subagent_creation={"task": "old worker"}, resumable=False)
        child_tools, child_config, _ = await twin_execution.assemble(product, child, context, include_memory=False)
        assert child_tools == [] and not child_config["execution_enabled"]
        assert personal.get_execution_config(request["id"]) is None
        # A new accepted prompt in the same now-idle SDK session gets the feature.
        session.resumable = False
        tools, fresh, _ = await twin_execution.assemble(product, session, context)
        assert fresh["execution_config"]["version"] == 1
        assert "bash" in {tool.name for tool in tools}
    asyncio.run(run())


def test_credential_continuation_receives_committed_shell_result_once(product, tmp_path, monkeypatch):
    from agent_runtime import AssistantMessageEventStream, assistant_message

    from app import personal, twin_execution
    from app.runtime_bridge import RuntimeBridge
    original_backend = twin_execution.TwinExecutionBackend
    monkeypatch.setattr(twin_execution, "TwinExecutionBackend", lambda identity, workspace:
        original_backend(identity, workspace, directory=tmp_path / "execution-state"))
    async def run():
        runtime = RuntimeBridge(product)
        await runtime.start()
        product.bind_runtime(runtime)
        calls = []
        def offline(model, context, options):
            calls.append(context.messages)
            position = len(calls)
            if position == 1:
                content = [{"type": "toolCall", "id": "prep", "name": "bash", "arguments": {
                    "command": "printf prepared-marker >> prepared.txt && printf preparation-completed"}}]
            elif position == 2:
                content = [{"type": "toolCall", "id": "credential", "name": "request_credential", "arguments": {
                    "name": "review-api", "description": "Review", "keynames": ["REVIEW_TOKEN"], "reason": "Second step"}}]
            else:
                content = [{"type": "text", "text": "Waiting for the dedicated credential form." if position == 3 else "Continued remaining work."}]
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=content,
                stopReason="toolUse" if position < 3 else "stop")})
            return stream
        runtime.models.register_api("offline-regression", offline)
        try:
            request = product.send_message("owner-alex", "private-alex", "Do the preparation and then the authenticated step.", key())["request"]
            await product.execute_request(request["id"])
            assert product.get_request(request["id"])["status"] == "completed"
            pending_row = credentials.list_requests(product, "owner-alex")["requests"][0]
            continued = await product.db_call(credentials.complete, product, "owner-alex", pending_row["id"], values={"REVIEW_TOKEN": "offline-fake-only"})
            original = product.get_request(request["id"])
            reads = []
            real_read = runtime._run_records
            async def tracked_read(tenant, session_id, run_id):
                reads.append((session_id, run_id))
                return await real_read(tenant, session_id, run_id)
            monkeypatch.setattr(runtime, "_run_records", tracked_read)
            await product.execute_request(continued["continuation_request_id"])
            assert product.get_request(continued["continuation_request_id"])["status"] == "completed"
            continuation_input = str(calls[-1])
            assert "Previously committed operations" in continuation_input
            assert "prepared-marker" in continuation_input
            assert "preparation-completed" in continuation_input
            assert "'outcome': 'completed'" in continuation_input or '"outcome": "completed"' in continuation_input
            assert "offline-fake-only" not in continuation_input
            assert reads.count((original["session_id"], original["run_id"])) == 1
            assert (personal.resolve_twin_workspace("owner-alex") / "prepared.txt").read_text() == "prepared-marker"
            assert len(calls) == 4
        finally:
            await runtime.close()
    asyncio.run(run())


def test_child_credential_wait_releases_worker_then_reuses_root_and_child_results(product, monkeypatch):
    from unittest.mock import AsyncMock

    from app.credential_models import CredentialRequest
    from app.runtime_bridge import RuntimeBridge, RuntimeBusy
    request, _, row = pending(product)
    with product.transaction() as db:
        origin = db.get(ReplyRequest, request["id"])
        origin.status, origin.session_id, origin.run_id = "completed", "root-session", "root-run"
        pending_row = db.get(CredentialRequest, row["id"])
        pending_row.runtime_session_id, pending_row.runtime_run_id = "child-session", "child-run"
        pending_row.runtime_command_id = "child-command"
    result = credentials.complete(product, "owner-alex", row["id"], values={"FAKE_ACCESS_TOKEN": "fake-child-only"})
    context = product.get_runtime_context(result["continuation_request_id"])
    def facts(marker):
        return [
            {"type": "run_started", "data": {"inputs": []}},
            {"type": "model_request", "data": {"step_id": "step"}},
            {"type": "model_completed", "data": {"message": {"content": [{"type": "toolCall", "id": "bash", "name": "bash", "arguments": {"command": marker}}]}}},
            {"type": "tool_started", "data": {"call_id": "bash", "args": {"command": marker}}},
            {"type": "tool_returned", "data": {"call_id": "bash", "result": {"content": [{"type": "text", "text": marker + " completed"}], "isError": False}}},
            {"id": marker + "-record", "type": "tool_completed", "data": {"call_id": "bash", "use_returned": True}},
            {"type": "run_completed", "data": {}},
        ]
    async def run():
        runtime = RuntimeBridge(product)
        state = {"child": "applied"}
        async def command(tenant, command_id):
            return {"state": "completed" if command_id == request["id"] else state["child"],
                "run_id": "root-run" if command_id == request["id"] else "child-run"}
        runtime.store = SimpleNamespace(get_command=command)
        root_records, child_records = facts("root-prepared"), facts("child-prepared")
        async def records(tenant, session_id, run_id):
            if session_id == "root-session":
                return root_records
            return child_records[:-1] if state["child"] == "applied" else child_records
        reader = AsyncMock(side_effect=records)
        monkeypatch.setattr(runtime, "_run_records", reader)
        with pytest.raises(RuntimeBusy):
            await runtime._credential_continuation_history(context)
        assert product.tasks == {}
        assert product.get_request(result["continuation_request_id"])["status"] == "queued"
        product.deferred[result["continuation_request_id"]] = 999999999
        product.wakeup = asyncio.Event()
        state["child"] = "completed"
        await runtime._wake_credential_continuations("child-session", "child-run")
        assert result["continuation_request_id"] not in product.deferred
        assert product.wakeup.is_set()
        reader.reset_mock()
        history = await runtime._credential_continuation_history(context)
        assert "root-prepared completed" in history and "child-prepared completed" in history
        assert reader.await_count == 2
        # A real unknown outcome must require reconciliation, never a silent
        # empty context or a retry of that started shell.
        child_records[-1] = {"type": "run_interrupted", "data": {"status": "waiting_recovery"}}
        state["child"] = "applied"
        child_records.insert(-1, {"type": "run_interrupted", "data": {"status": "waiting_recovery"}})
        with pytest.raises(RuntimeError, match="reconciliation"):
            await runtime._credential_continuation_history(context)
    asyncio.run(run())
