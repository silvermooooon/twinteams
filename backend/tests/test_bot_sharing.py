"""Snapshot sharing against real PostgreSQL, SDK Git Memory and skill packs."""
import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from agent_runtime.memory import MemorySource
from agent_runtime.sessions import SessionInfo
from cryptography.fernet import Fernet
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, func, inspect, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from test_memory_dream import product as product  # noqa: PLC0414

from app import (
    api,
    bot_api,
    bot_sharing,
    bot_skills,
    bot_snapshot_store,
    config,
    credentials,
    personal,
    personal_api,
    personal_bots,
)
from app import service as service_module
from app.bot_sharing_schema import check_bot_sharing_schema, upgrade_bot_sharing_schema
from app.models import (
    Actor,
    BotInstall,
    BotSnapshot,
    Membership,
    Message,
    ReplyRequest,
    TrackingTopic,
)
from app.runtime_bridge import RuntimeBridge, text_content


@pytest.fixture
def sharing(product, monkeypatch, tmp_path):
    local = replace(service_module.settings, workspace_root=tmp_path / "workspace")
    for module in (config, personal, bot_snapshot_store, service_module):
        monkeypatch.setattr(module, "settings", local)
    for module in (api, bot_api, personal_api):
        monkeypatch.setattr(module, "service", product)
    monkeypatch.setenv("AGENT_SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("AGENT_SECRETS_DIR", str(tmp_path / "host-secrets"))
    return product


@pytest.fixture
def client(sharing):
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(bot_api.router)
    app.include_router(personal_api.router)

    def identity(x_test_owner: Annotated[str, Header()] = "owner-alex"):
        return x_test_owner

    app.dependency_overrides[api.identity] = identity
    with TestClient(app) as client:
        yield client


def create(product, owner="owner-alex"):
    return personal_bots.create_bot(product, owner, "Research", "Check claims against evidence.")


def memory(bot, owner="owner-alex"):
    return personal.make_bot_memory_store(SessionInfo("share-test", personal.settings.tenant_id, owner), bot["id"])


def write(store, key, content):
    current = asyncio.run(store.read(key))
    return asyncio.run(store.write(key, content, sources=[MemorySource("synthetic-session", "synthetic-event")],
                                   expected_version=current.version if current else None))


def freeze(product, bot, owner="owner-alex"):
    return bot_sharing.freeze_share(product, owner, bot["id"], uuid4().hex)


def seed_skill(owner="owner-alex", content="Use clear evidence."):
    root = personal.resolve_twin_workspace(owner) / "skills" / "evidence"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\ndescription: Check evidence\n---\n" + content)
    (root / "references").mkdir()
    (root / "references" / "check.md").write_text("Check the source.")
    with personal.service.transaction() as db:
        personal.service.twin(db, owner).enabled_skill_names = ["evidence"]
    return root


def test_snapshot_memory_is_frozen_and_installs_evolve_independently(sharing):
    source = create(sharing)
    source_store = memory(source)
    write(source_store, "MEMORY", "At sharing time")
    old = write(source_store, "removed", "Removed before sharing")
    removed_blob = source_store._git(Path(source_store.remote), "rev-parse", "main:removed.md").stdout.strip()
    asyncio.run(source_store.delete("removed", expected_version=old.version))
    shared = freeze(sharing, source)
    write(source_store, "MEMORY", "Source later")

    copies = [bot_sharing.install_share(sharing, owner, shared["token"]["token"])
              for owner in ("owner-maya", "owner-jordan")]
    stores = [memory(bot, owner) for bot, owner in zip(copies, ("owner-maya", "owner-jordan"))]
    assert all(asyncio.run(store.read("MEMORY")).content == "At sharing time" for store in stores)
    assert all(asyncio.run(store.read("removed")) is None for store in stores)
    for store in stores:
        assert store._git(Path(store.remote), "cat-file", "-e", removed_blob.decode(), check=False).returncode != 0
    write(stores[0], "MEMORY", "Maya learns something")
    write(stores[1], "MEMORY", "Jordan learns something else")
    assert asyncio.run(source_store.read("MEMORY")).content == "Source later"
    assert asyncio.run(stores[0].read("MEMORY")).content == "Maya learns something"
    assert asyncio.run(stores[1].read("MEMORY")).content == "Jordan learns something else"
    for owner, bot in zip(("owner-maya", "owner-jordan"), copies):
        assert bot["installed_snapshot_id"] == shared["snapshot"]["id"]
        assert bot["chat"]["kind"] == "bot" and bot["actor"]["owner_id"] == owner
        assert bot["chat"]["member_owner_ids"] == [owner]
    assert bot_sharing.install_share(sharing, "owner-maya", shared["token"]["token"])["id"] == copies[0]["id"]


def test_snapshot_skills_prompts_and_original_lineage_are_independent(sharing):
    seed_skill()
    source = create(sharing)
    shared = freeze(sharing, source)
    token = shared["token"]["token"]
    maya = bot_sharing.install_share(sharing, "owner-maya", token)
    jordan = bot_sharing.install_share(sharing, "owner-jordan", token)
    original_config = asyncio.run(bot_skills.build_skill_config("owner-maya", maya["id"]))
    assert original_config["skills"] == [{"name": "evidence", "description": "Check evidence"}]
    assert not (personal.resolve_twin_workspace("owner-maya") / "skills" / "evidence").exists()
    asyncio.run(bot_skills.edit_skill("owner-maya", maya["id"], "evidence",
                                      "---\ndescription: Check evidence\n---\nMaya's own instructions."))
    personal_bots.edit_bot(sharing, "owner-maya", maya["id"], "Independent research", "Maya's assignment")
    personal_bots.edit_bot(sharing, "owner-alex", source["id"], "Source research", "Source assignment")
    write(memory(maya, "owner-maya"), "maya-learned", "Learned after the first install")
    for owner, bot in (("owner-alex", source), ("owner-jordan", jordan)):
        assert asyncio.run(bot_skills.skill_detail(owner, bot["id"], "evidence"))["content"].endswith("Use clear evidence.")
        assert asyncio.run(memory(bot, owner).read("maya-learned")) is None
    assert personal_bots.list_bots(sharing, "owner-jordan")[0]["assignment"] == "Check claims against evidence."
    evolved = freeze(sharing, maya, "owner-maya")
    assert evolved["snapshot"]["parent_snapshot_id"] == shared["snapshot"]["id"]
    assert evolved["snapshot"]["id"] != shared["snapshot"]["id"]
    evolved_copy = bot_sharing.install_share(sharing, "owner-alex", evolved["token"]["token"])
    assert evolved_copy["assignment"] == "Maya's assignment"
    assert asyncio.run(bot_skills.skill_detail("owner-alex", evolved_copy["id"], "evidence"))["content"].endswith("Maya's own instructions.")
    assert asyncio.run(memory(evolved_copy).read("maya-learned")).content == "Learned after the first install"
    reshared = bot_sharing.issue_token(sharing, "owner-maya", maya["id"], shared["snapshot"]["id"], uuid4().hex)
    preview = bot_sharing.preview_share(sharing, "owner-jordan", reshared["token"])
    assert preview["snapshot"]["id"] == shared["snapshot"]["id"]
    assert preview["installed_bot"]["id"] == jordan["id"]
    catalog = bot_sharing.list_shares(sharing, "owner-maya", maya["id"])
    assert catalog["installed_snapshot"]["id"] == shared["snapshot"]["id"]
    assert [row["id"] for row in catalog["snapshots"]] == [evolved["snapshot"]["id"]]
    assert {row["id"] for row in catalog["installed_snapshot"]["tokens"]} == {reshared["id"]}


def test_install_reuses_objects_and_is_idempotent_under_concurrent_requests(sharing):
    seed_skill()
    source = create(sharing)
    write(memory(source), "MEMORY", "Shared memory object")
    shared = freeze(sharing, source)
    token = shared["token"]["token"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        copies = list(pool.map(lambda _: bot_sharing.install_share(sharing, "owner-maya", token), range(4)))
    assert len({bot["id"] for bot in copies}) == 1
    other = bot_sharing.install_share(sharing, "owner-jordan", token)
    stores = [memory(copies[0], "owner-maya"), memory(other, "owner-jordan")]
    alternates = []
    for store in stores:
        objects = Path(store.remote) / "objects"
        alternates.append((objects / "info" / "alternates").read_text().strip())
        assert not [p for p in objects.rglob("*") if p.is_file() and p.parent.name != "info"]
    assert alternates[0] == alternates[1]
    shared_objects = Path(alternates[0])
    before = {str(p.relative_to(shared_objects)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in shared_objects.rglob("*") if p.is_file()}
    write(stores[0], "MEMORY", "A new local commit")
    after = {str(p.relative_to(shared_objects)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in shared_objects.rglob("*") if p.is_file()}
    assert after == before
    with sharing.transaction() as db:
        assert db.scalar(select(func.count()).select_from(BotInstall)) == 2
        assert db.get(Actor, copies[0]["id"]).bot_skill_pack_digest == db.get(Actor, other["id"]).bot_skill_pack_digest
        snapshot = db.get(BotSnapshot, shared["snapshot"]["id"])
        assert stores[1]._git(Path(stores[1].remote), "rev-parse", "main").stdout.decode().strip() == snapshot.memory_commit


def test_revoking_one_token_blocks_new_installs_without_changing_copies(sharing):
    source = create(sharing)
    shared = freeze(sharing, source)
    copy = bot_sharing.install_share(sharing, "owner-maya", shared["token"]["token"])
    second = bot_sharing.issue_token(sharing, "owner-alex", source["id"], shared["snapshot"]["id"], uuid4().hex)
    bot_sharing.revoke_token(sharing, "owner-alex", source["id"], shared["token"]["id"])
    assert bot_sharing.preview_share(sharing, "owner-maya", shared["token"]["token"])["installed_bot"]["id"] == copy["id"]
    assert bot_sharing.install_share(sharing, "owner-maya", shared["token"]["token"])["id"] == copy["id"]
    with pytest.raises(HTTPException) as denied:
        bot_sharing.install_share(sharing, "owner-jordan", shared["token"]["token"])
    assert denied.value.status_code in {404, 410}
    assert personal_bots.list_bots(sharing, "owner-maya")[0]["id"] == copy["id"]
    assert bot_sharing.install_share(sharing, "owner-jordan", second["token"])["installed_snapshot_id"] == shared["snapshot"]["id"]
    own_token = bot_sharing.issue_token(sharing, "owner-maya", copy["id"], shared["snapshot"]["id"], uuid4().hex)
    assert bot_sharing.preview_share(sharing, "owner-jordan", own_token["token"])["snapshot"]["id"] == shared["snapshot"]["id"]


def test_share_permissions_and_copy_owner_boundaries(sharing, client):
    source = create(sharing)
    other = {"x-test-owner": "owner-maya"}
    for bot_id in (source["id"], "twin-alex", "missing"):
        response = client.post(f"/api/bots/{bot_id}/shares", headers=other, json={"client_request_key": uuid4().hex})
        assert response.status_code in {403, 404}
    assert client.post("/api/bots/twin-alex/shares", json={"client_request_key": uuid4().hex}).status_code in {403, 404}
    created = client.post(f"/api/bots/{source['id']}/shares", json={"client_request_key": uuid4().hex})
    assert created.status_code == 200
    shared = created.json()
    assert len(shared["token"]["token"]) >= 32
    assert client.get(f"/api/bots/{source['id']}/shares", headers=other).status_code in {403, 404}
    assert client.delete(f"/api/bots/{source['id']}/shares/tokens/{shared['token']['id']}", headers=other).status_code in {403, 404}
    installed = client.post("/api/bot-shares/install", json={"token": shared["token"]["token"]}, headers=other)
    assert installed.status_code == 200
    copy = installed.json()
    assert copy["id"] != source["id"]
    assert client.get(f"/api/chats/{copy['chat_id']}").status_code in {403, 404}
    assert client.get(f"/api/bots/{copy['id']}/history").status_code in {403, 404}
    assert client.patch(f"/api/bots/{copy['id']}", json={"name": "Wrong owner", "assignment": "Take over"}).status_code in {403, 404}
    assert client.post("/api/bots/messages", json={"sender_actor_id": source["id"], "recipient_actor_id": copy["id"],
        "body": "No cross-owner agents", "client_request_key": uuid4().hex}).status_code in {403, 404}
    assert client.post("/api/bot-rooms", json={"title": "Cannot invite another owner's BOT", "bot_ids": [copy["id"]]}).status_code in {403, 404}
    assert client.post("/api/chats", headers=other, json={"owner_ids": [copy["id"]], "title": "No BOT in human chat"}).status_code in {400, 403, 404}
    # A source owner may obtain a separate copy; the source identity must never be replaced.
    self_install = client.post("/api/bot-shares/install", json={"token": shared["token"]["token"]})
    assert self_install.status_code in {200, 409}
    if self_install.status_code == 200:
        assert self_install.json()["id"] != source["id"]
    assert source["id"] in {bot["id"] for bot in personal_bots.list_bots(sharing, "owner-alex")}


def test_snapshot_includes_bot_rules_and_avatar_but_not_private_resources(sharing, tmp_path):
    source = create(sharing)
    workspace = personal.settings.workspace_root / source["chat_id"]
    workspace.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("BOT's own behavior rules.")
    (workspace / "AGENTS.md").write_text("BOT-specific constraints.")
    (workspace / ".env").write_text("TOKEN=synthetic-secret-do-not-share")
    (workspace / "report.txt").write_text("Unrelated workspace artifact")
    main_workspace = personal.resolve_twin_workspace("owner-alex")
    main_workspace.mkdir(parents=True, exist_ok=True)
    (main_workspace / "SOUL.md").write_text("Main Duet private behavior preference")
    asyncio.run(credentials.owner_store("owner-alex").put("synthetic-login", {"ACCESS_TOKEN": "synthetic-secret-value"}))
    avatar = b"synthetic-webp-avatar-bytes"
    with sharing.transaction() as db:
        actor = db.get(Actor, source["id"])
        actor.avatar_data, actor.avatar_digest = avatar, hashlib.sha256(avatar).hexdigest()
        db.add(TrackingTopic(chat_id=source["chat_id"], title="Private follow-up", description="Private Tracking",
                             subject_key="share-test", source_floor_seq=1))
    sharing.send_message("owner-alex", source["chat_id"], "Private source conversation", uuid4().hex)
    shared = freeze(sharing, source)
    copy = bot_sharing.install_share(sharing, "owner-maya", shared["token"]["token"])
    copied_workspace = personal.settings.workspace_root / copy["chat_id"]
    assert (copied_workspace / "SOUL.md").read_text() == "BOT's own behavior rules."
    assert (copied_workspace / "AGENTS.md").read_text() == "BOT-specific constraints."
    assert not (copied_workspace / ".env").exists() and not (copied_workspace / "report.txt").exists()
    assert asyncio.run(credentials.owner_store("owner-maya").list()) == []
    assert personal.avatar_file("owner-maya", copy["id"])[0] == avatar
    with sharing.transaction() as db:
        assert db.get(Actor, copy["id"]).avatar_data is None
        assert db.scalar(select(func.count()).select_from(Message).where(Message.chat_id == copy["chat_id"])) == 0
        assert db.scalar(select(func.count()).select_from(ReplyRequest).where(ReplyRequest.chat_id == copy["chat_id"])) == 0
        assert db.scalar(select(func.count()).select_from(TrackingTopic).where(TrackingTopic.chat_id == copy["chat_id"])) == 0
        assert list(db.scalars(select(Membership.owner_id).where(Membership.chat_id == copy["chat_id"]))) == ["owner-maya"]
    serialized = json.dumps(bot_sharing.preview_share(sharing, "owner-maya", shared["token"]["token"]))
    assert "synthetic-secret" not in serialized and "Private source conversation" not in serialized


def test_installed_bot_runtime_uses_snapshot_skills_rules_and_recipient_credentials(sharing):
    seed_skill()
    source = create(sharing)
    workspace = personal.settings.workspace_root / source["chat_id"]
    workspace.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("Ask for evidence before claiming certainty.")
    shared = freeze(sharing, source)
    copied = bot_sharing.install_share(sharing, "owner-maya", shared["token"]["token"])
    seed_skill("owner-maya", "Recipient main skill must remain separate.")
    main_workspace = personal.resolve_twin_workspace("owner-maya")
    (main_workspace / "SOUL.md").write_text("Recipient main Soul must stay private.")
    asyncio.run(credentials.owner_store("owner-alex").put("source-only", {"SOURCE_TOKEN": "source-synthetic-value"}))
    asyncio.run(credentials.owner_store("owner-maya").put("recipient-only", {"RECIPIENT_TOKEN": "recipient-synthetic-value"}))

    async def run():
        runtime = RuntimeBridge(sharing)

        def offline(model, context, options):
            system = text_content(context.messages[0])
            assert "Check claims against evidence." in system
            assert "Ask for evidence before claiming certainty." in system
            assert "Recipient main Soul must stay private." not in system
            results = {row["toolName"]: row for row in context.messages if row["role"] == "toolResult"}
            assert all(not row.get("isError") for row in results.values())
            if "load_skill" not in results:
                name, args = "load_skill", {"name": "evidence"}
            elif "memory_write" not in results:
                assert "Use clear evidence." in text_content(results["load_skill"])
                assert "Recipient main skill" not in text_content(results["load_skill"])
                name, args = "memory_write", {"key": "runtime-check", "content": "Recipient runtime saved this", "expected_version": None}
            else:
                name, args = None, None
            content = ([{"type": "toolCall", "id": uuid4().hex, "name": name, "arguments": args}]
                       if name else [{"type": "text", "text": "The installed BOT used its shared configuration."}])
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=content, stopReason="toolUse" if name else "stop")})
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        sharing.bind_runtime(runtime)
        try:
            request = sharing.send_message("owner-maya", copied["chat_id"], "Use the installed skill", uuid4().hex)["request"]
            await sharing.execute_request(request["id"])
            completed = sharing.get_request(request["id"])
            assert completed["status"] == "completed", completed["error"]
            with sharing.transaction() as db:
                frozen = db.get(ReplyRequest, request["id"]).execution_config
                assert frozen["bot_skill_pack_digest"] == db.get(Actor, copied["id"]).bot_skill_pack_digest
                assert [row["ref"] for row in frozen["credentials"]] == ["recipient-only"]
                assert "recipient-synthetic-value" not in json.dumps(frozen)
                assert "source-synthetic-value" not in json.dumps(frozen)
            assert (await memory(copied, "owner-maya").read("runtime-check")).content == "Recipient runtime saved this"
            assert await memory(source).read("runtime-check") is None
        finally:
            await runtime.close()

    asyncio.run(run())


def test_share_http_preview_list_reissue_revoke_and_request_idempotency(sharing, client):
    source = create(sharing)
    base = f"/api/bots/{source['id']}/shares"
    assert client.get(base).json() == {"snapshots": [], "installed_snapshot": None}
    payload = {"client_request_key": uuid4().hex}
    shared = client.post(base, json=payload).json()
    assert client.post(base, json=payload).json() == shared
    invitation = {"token": shared["token"]["token"]}
    preview = client.post("/api/bot-shares/preview", json=invitation, headers={"x-test-owner": "owner-maya"})
    assert preview.status_code == 200 and preview.json()["installed_bot"] is None
    assert {"name", "skill_count", "memory_included", "source_bot_id"} <= preview.json()["snapshot"].keys()
    assert "tokens" not in preview.json()["snapshot"]
    installed = client.post("/api/bot-shares/install", json=invitation, headers={"x-test-owner": "owner-maya"})
    assert installed.status_code == 200
    assert client.post("/api/bot-shares/install", json=invitation, headers={"x-test-owner": "owner-maya"}).json()["id"] == installed.json()["id"]
    token_route = f"{base}/{shared['snapshot']['id']}/tokens"
    payload = {"client_request_key": uuid4().hex}
    second = client.post(token_route, json=payload)
    assert second.status_code == 200
    assert client.post(token_route, json=payload).json() == second.json()
    assert len(client.get(base).json()["snapshots"][0]["tokens"]) == 2
    assert client.delete(f"{base}/tokens/{shared['token']['id']}").status_code == 200
    assert client.post("/api/bot-shares/preview", json=invitation).status_code in {404, 410}
    assert client.post("/api/bot-shares/install", json=invitation, headers={"x-test-owner": "owner-jordan"}).status_code in {404, 410}


def test_share_token_does_not_replace_sign_in(sharing):
    source = create(sharing)
    shared = freeze(sharing, source)
    app = FastAPI()
    app.include_router(bot_api.router)
    with TestClient(app) as anonymous:
        invitation = {"token": shared["token"]["token"]}
        assert anonymous.post("/api/bot-shares/preview", json=invitation).status_code == 401
        assert anonymous.post("/api/bot-shares/install", json=invitation).status_code == 401


def test_bot_skill_http_upload_edit_toggle_delete_and_owner_permissions(sharing, client):
    bot = create(sharing)
    route = f"/api/bots/{bot['id']}/skills"
    detail_route = route + "/evidence"
    other = {"x-test-owner": "owner-maya"}
    assert client.get(route).json() == {"items": []}
    files = [("files", ("evidence/SKILL.md", b"---\ndescription: Check evidence\n---\nUploaded instructions.", "text/markdown")),
             ("files", ("evidence/references/source.md", b"Read the original source.", "text/markdown"))]
    assert client.post(route + "/upload", files=files, headers=other).status_code in {403, 404}
    uploaded = client.post(route + "/upload", files=files)
    assert uploaded.status_code == 200
    assert uploaded.json()["items"] == [{"name": "evidence", "description": "Check evidence",
                                        "enabled": False, "available": True, "error": None}]
    assert client.get(route, headers=other).status_code in {403, 404}
    assert client.get(detail_route, headers=other).status_code in {403, 404}
    detail = client.get(detail_route)
    assert detail.status_code == 200 and detail.json()["content"].endswith("Uploaded instructions.")
    assert "references/source.md" in detail.json()["references"]
    assert client.patch(detail_route, json={"enabled": True}, headers=other).status_code in {403, 404}
    enabled = client.patch(detail_route, json={"enabled": True})
    assert enabled.status_code == 200 and enabled.json()["enabled"]
    edit = {"content": "---\ndescription: Review evidence\n---\nEdited instructions."}
    assert client.put(detail_route, json=edit, headers=other).status_code in {403, 404}
    edited = client.put(detail_route, json=edit)
    assert edited.status_code == 200 and edited.json()["content"].endswith("Edited instructions.")
    assert "references/source.md" in edited.json()["references"]
    assert client.get(route).json()["items"][0]["description"] == "Review evidence"
    assert not (personal.resolve_twin_workspace("owner-alex") / "skills" / "evidence").exists()
    assert client.delete(detail_route, headers=other).status_code in {403, 404}
    deleted = client.delete(detail_route)
    assert deleted.status_code == 200 and deleted.json() == {"items": []}
    assert client.get(detail_route).status_code == 404


@pytest.mark.parametrize("fixture_kind", ["credential_file", "workspace_symlink"])
def test_share_rejects_credential_rule_files_and_symlinked_bot_workspace(sharing, tmp_path, fixture_kind):
    bot = create(sharing)
    workspace = personal.settings.workspace_root / bot["chat_id"]
    workspace.parent.mkdir(parents=True, exist_ok=True)
    if fixture_kind == "credential_file":
        (workspace / "rules").mkdir(parents=True)
        (workspace / "rules" / "credentials.json").write_text('{"token":"synthetic-sensitive-value"}')
    else:
        target = tmp_path / "unrelated-workspace"
        target.mkdir()
        (target / "SOUL.md").write_text("Unrelated rules")
        workspace.symlink_to(target, target_is_directory=True)
    with pytest.raises(HTTPException) as rejected:
        freeze(sharing, bot)
    assert rejected.value.status_code == 422
    with sharing.transaction() as db:
        assert db.scalar(select(func.count()).select_from(BotSnapshot)) == 0


def test_snapshot_schema_upgrade_validation_immutability_and_reference_retention(sharing):
    source = create(sharing)
    first = freeze(sharing, source)
    second = freeze(sharing, source)
    assert second["snapshot"]["parent_snapshot_id"] == first["snapshot"]["id"]
    copy = bot_sharing.install_share(sharing, "owner-maya", first["token"]["token"])
    upgrade_bot_sharing_schema(service_module.engine)
    upgrade_bot_sharing_schema(service_module.engine)
    check_bot_sharing_schema(inspect(service_module.engine))
    with pytest.raises(ProgrammingError, match="immutable"), sharing.transaction() as db:
        db.get(BotSnapshot, first["snapshot"]["id"]).name = "Cannot rewrite the shared node"
    bot_sharing.revoke_token(sharing, "owner-alex", source["id"], first["token"]["id"])
    with pytest.raises(IntegrityError), sharing.transaction() as db:
        db.delete(db.get(BotSnapshot, first["snapshot"]["id"]))
    assert personal_bots.list_bots(sharing, "owner-maya")[0]["id"] == copy["id"]
    with service_module.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER tt_bot_snapshot_immutable ON tt_bot_snapshots"))
    with pytest.raises(RuntimeError, match="app.init_db"):
        check_bot_sharing_schema(inspect(service_module.engine))
    upgrade_bot_sharing_schema(service_module.engine)
    check_bot_sharing_schema(inspect(service_module.engine))


def test_additive_upgrade_from_pre_sharing_schema_preserves_bots(sharing):
    source = create(sharing)
    with service_module.engine.begin() as connection:
        connection.execute(text("ALTER TABLE tt_actors DROP CONSTRAINT tt_actor_installed_snapshot"))
        connection.execute(text("DROP TABLE tt_bot_installs, tt_bot_share_tokens, tt_bot_snapshots"))
        connection.execute(text("ALTER TABLE tt_actors DROP COLUMN bot_skill_pack_digest, DROP COLUMN installed_snapshot_id"))
    with pytest.raises(RuntimeError, match="app.init_db"):
        check_bot_sharing_schema(inspect(service_module.engine))
    upgrade_bot_sharing_schema(service_module.engine)
    check_bot_sharing_schema(inspect(service_module.engine))
    existing, = personal_bots.list_bots(sharing, "owner-alex")
    assert existing["id"] == source["id"] and existing["assignment"] == source["assignment"]
    shared = freeze(sharing, source)
    assert bot_sharing.install_share(sharing, "owner-maya", shared["token"]["token"])["name"] == source["name"]


def test_share_catalog_has_constant_query_count_and_no_memory_or_skill_bodies(sharing):
    source = create(sharing)
    write(memory(source), "MEMORY", "Body must not appear in list")
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    counts = []
    for count in (1, 12):
        for _ in range(1 if count == 12 else 0, count):
            shared = freeze(sharing, source)
            bot_sharing.issue_token(sharing, "owner-alex", source["id"], shared["snapshot"]["id"], uuid4().hex)
        event.listen(service_module.engine, "before_cursor_execute", record)
        try:
            statements.clear()
            result = bot_sharing.list_shares(sharing, "owner-alex", source["id"])
            counts.append(len(statements))
        finally:
            event.remove(service_module.engine, "before_cursor_execute", record)
        assert len(result["snapshots"]) == count
        assert "Body must not appear in list" not in json.dumps(result)
        assert all("memory_commit" not in row and "skill_inventory" not in row for row in result["snapshots"])
    assert counts[0] == counts[1], counts
