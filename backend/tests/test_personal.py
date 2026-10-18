"""Personal pages against an isolated PG schema, local files and no model calls."""
import asyncio
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import inspect, select

from app.main import app
from app import personal
from app.models import Actor, Chat
from app.notifications import notifications
from app.service import service


def client_for(name="DEV-AAA"):
    service.initialize(create_schema=True)
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"username": name}).status_code == 200
    return client


def test_profile_avatar_preset_seed_and_private_title():
    client = client_for()
    original = client.get("/api/me/twin").json()["actor"]
    try:
        changed = client.patch("/api/me/twin", json={"name": "Personal Twin", "avatar_preset": "orbit"}).json()
        assert changed["name"] == "Personal Twin" and changed["avatar_preset"] == "orbit"
        assert changed["profile_revision"] > original["profile_revision"]
        assert "enabled_skill_names" not in changed and "avatar_data" not in changed
        service.seed_local()
        assert client.get("/api/me/twin").json()["actor"]["name"] == "Personal Twin"
        chats = client.get("/api/bootstrap").json()["chats"]
        assert next(chat for chat in chats if chat["kind"] == "private")["title"] == "Personal Twin"
        assert client.patch("/api/me/twin", json={"name": "   "}).status_code == 422
        assert client.patch("/api/me/twin", json={"name": None}).status_code == 422
        assert client.patch("/api/me/twin", json={"avatar_preset": "unknown"}).status_code == 422
        buffer = BytesIO()
        Image.new("RGB", (400, 200), "red").save(buffer, format="PNG")
        uploaded = client.post("/api/me/twin/avatar", content=buffer.getvalue()).json()
        assert uploaded["avatar_preset"] is None and uploaded["avatar_url"]
        public = client_for("DEV-BBB").get("/api/actors/" + uploaded["id"]).json()
        assert public["avatar_url"] == uploaded["avatar_url"]
        avatar = client.get(uploaded["avatar_url"])
        assert avatar.status_code == 200 and avatar.headers["content-type"] == "image/webp"
        assert Image.open(BytesIO(avatar.content)).size == (256, 256)
        assert client.get(uploaded["avatar_url"], headers={"If-None-Match": avatar.headers["etag"]}).status_code == 304
        assert "private" in avatar.headers["cache-control"]
        assert TestClient(app).get(uploaded["avatar_url"]).status_code == 401
        assert client.post("/api/me/twin/avatar", content=b"not an image").status_code == 422
        assert client.delete("/api/me/twin/avatar").json()["avatar_url"] is None
        with service.transaction() as db:
            actor = db.get(Actor, uploaded["id"])
            assert "avatar_data" in inspect(actor).unloaded
            service.actor_json(actor)
            assert "avatar_data" in inspect(actor).unloaded
    finally:
        client.patch("/api/me/twin", json={"name": original["name"], "avatar_preset": original["avatar_preset"]})


def test_skills_scope_invalid_deleted_and_snapshot():
    client = client_for()
    root = personal.resolve_twin_workspace("owner-alex") / "skills"
    name = "report-" + uuid4().hex
    directory = root / name
    (directory / "references").mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\ndescription: Write reports\n---\nInstructions", encoding="utf-8")
    (directory / "references/style.md").write_text("Short", encoding="utf-8")
    bad = root / ("broken-" + uuid4().hex)
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\ndescription: [\n---\nBad", encoding="utf-8")
    before = client.get("/api/me/twin/skills").json()["items"]
    assert next(item for item in before if item["name"] == name)["enabled"] is False
    assert next(item for item in before if item["name"] == bad.name)["available"] is False
    assert client.patch(f"/api/me/twin/skills/{bad.name}", json={"enabled": True}).status_code == 422
    assert client.patch(f"/api/me/twin/skills/{name}", json={"enabled": True}).status_code == 200
    assert not any(item["name"] == name for item in client_for("DEV-BBB").get("/api/me/twin/skills").json()["items"])
    config = asyncio.run(personal.build_skill_config("owner-alex"))
    assert {"name": name, "description": "Write reports"} in config
    store = personal.create_enabled_skill_store("owner-alex", config)
    assert asyncio.run(store.load(name)).content.endswith("Instructions")
    assert asyncio.run(store.load_reference(name, "references/style.md")) == "Short"
    with service.transaction() as db:
        request = service.accept_request(db, db.get(Chat, "private-alex"), "twin-alex", "owner-alex", "manual", uuid4().hex)
        request_id = request.id
    assert personal.get_execution_config(request_id) is None
    frozen = {"version": 1, "skills": config, "credentials": []}
    assert personal.freeze_execution_config(request_id, frozen) == frozen
    assert client.patch(f"/api/me/twin/skills/{name}", json={"enabled": False}).status_code == 200
    assert personal.freeze_execution_config(request_id, {"skills": []}) == frozen
    # Original task's store survives later switch changes; next task is empty for this name.
    assert asyncio.run(store.load(name)).name == name
    next_store = personal.create_enabled_skill_store("owner-alex", asyncio.run(personal.build_skill_config("owner-alex")))
    for call in (lambda: next_store.load(name), lambda: next_store.load_reference(name, "references/style.md")):
        try:
            asyncio.run(call())
        except PermissionError:
            pass
        else:
            raise AssertionError("Disabled skill was read")
    client.patch(f"/api/me/twin/skills/{name}", json={"enabled": True})
    (directory / "SKILL.md").unlink()
    missing = next(item for item in client.get("/api/me/twin/skills").json()["items"] if item["name"] == name)
    assert missing["enabled"] and not missing["available"]
    assert client.patch(f"/api/me/twin/skills/{name}", json={"enabled": False}).status_code == 200
    assert "enabled_skill_names" not in str(client.get("/api/bootstrap").json())


def test_memory_read_only_pagination_and_owner_scope():
    from agent_runtime.memory import MemorySource
    client = client_for()
    store = personal.own_memory_store("owner-alex")
    prefix = "test-" + uuid4().hex
    for suffix in ("a", "b", "c"):
        asyncio.run(store.write(prefix + "/" + suffix, "Remember " + suffix,
                               sources=(MemorySource("opaque-source", "event-ref"),), expected_version=None))
    page = client.get("/api/me/twin/memory", params={"limit": 2, "after": prefix}).json()
    assert page["enabled"] is True and len(page["items"]) == 2 and page["next_cursor"] == prefix + "/b"
    item = client.get("/api/me/twin/memory/entry", params={"key": prefix + "/a"}).json()
    assert item["sources"] == [{"session_id": "opaque-source", "event_id": "event-ref"}]
    assert client.get("/api/me/twin/memory/search", params={"q": "Remember"}).json()["items"]
    assert client_for("DEV-BBB").get("/api/me/twin/memory/entry", params={"key": prefix + "/a"}).status_code == 404
    assert client.get("/api/me/twin/memory/entry", params={"key": "../escape"}).status_code == 422
    assert client.post("/api/me/twin/memory", json={}).status_code == 405


def test_runtime_worker_metadata_and_targeted_notifications():
    from app.websocket import SocketHub, Client
    client = client_for()
    previous = notifications.worker_status
    try:
        notifications.worker_status = {"configured": True, "provider": "actual", "model": "resolved", "api": "responses", "expires_at": 1, "memory": {"enabled": False, "mode": "disabled"}}
        status = client.get("/api/me/twin/runtime").json()
        assert status["configured"] is False and status["worker"]["online"] is False
        assert status["provider"] == "actual" and status["memory"]["enabled"] is False
        assert "workspace" not in status and "endpoint" not in status
        hub = SocketHub()
        aaa, bbb = Client(None, "owner-alex", 9999999999), Client(None, "owner-maya", 9999999999)
        hub.clients.update({aaa, bbb})
        hub.notice({"type": "personal_changed", "owner_id": "owner-alex", "section": "skills"})
        assert aaa.queue.get_nowait() == {"v": 1, "type": "personal_changed", "section": "skills"}
        assert bbb.queue.empty()
        hub.notice({"type": "actor_changed", "actor_id": "twin-alex", "profile_revision": 2})
        assert aaa.queue.get_nowait()["actor_id"] == bbb.queue.get_nowait()["actor_id"] == "twin-alex"
    finally:
        notifications.worker_status = previous


def test_additive_personal_migration_is_repeatable_and_keeps_existing_rows():
    from sqlalchemy import create_engine, text
    from checks.postgres import TestDatabase
    from app.personal_schema import PERSONAL_COLUMNS, upgrade_personal_schema
    database = TestDatabase()
    engine = create_engine(database.url.replace("postgresql://", "postgresql+psycopg://"))
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE tt_owners (id VARCHAR(80) PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE tt_chats (id VARCHAR(80) PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE tt_activity_runs (session_id TEXT PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE tt_actors (id TEXT PRIMARY KEY, name TEXT NOT NULL)"))
            connection.execute(text("CREATE TABLE tt_reply_requests (id TEXT PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE tt_credential_requests (id TEXT PRIMARY KEY, ref TEXT NOT NULL, status TEXT NOT NULL)"))
            connection.execute(text("INSERT INTO tt_actors (id,name) VALUES ('kept','Custom Twin')"))
            connection.execute(text("INSERT INTO tt_credential_requests (id,ref,status) VALUES ('pending-kept','kept-ref','pending')"))
        upgrade_personal_schema(engine)
        upgrade_personal_schema(engine)
        for table, columns in PERSONAL_COLUMNS.items():
            assert set(columns).issubset({item["name"] for item in inspect(engine).get_columns(table)})
        with engine.connect() as connection:
            row = connection.execute(text("SELECT name,profile_revision,enabled_skill_names FROM tt_actors WHERE id='kept'")).one()
            assert tuple(row) == ("Custom Twin", 1, [])
            row = connection.execute(text("SELECT ref,status,runtime_session_id,runtime_run_id,runtime_command_id FROM tt_credential_requests WHERE id='pending-kept'")).one()
            assert tuple(row) == ("kept-ref", "pending", None, None, None)
    finally:
        engine.dispose()
        database.close()
