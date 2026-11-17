"""BOT packs exercise the real SDK Skill parser and content-addressed files."""
import asyncio
import os
import shlex
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from fastapi import HTTPException
from test_memory_dream import product as product  # noqa: PLC0414

from app import bot_skills, bot_snapshot_store, personal, personal_bots
from app.models import Actor
from app.runtime_bridge import RuntimeBridge, text_content


@pytest.fixture
def skill_scope(product, monkeypatch, tmp_path):
    settings = replace(personal.settings, workspace_root=tmp_path / "workspace")
    monkeypatch.setattr(personal, "settings", settings)
    monkeypatch.setattr(bot_snapshot_store, "settings", settings)
    return product


def make_bot(product, name="Research"):
    return personal_bots.create_bot(product, "owner-alex", name, "Review assigned evidence.")


def main_skill(name="reports"):
    root = personal.resolve_twin_workspace("owner-alex") / "skills" / name
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("---\ndescription: Review reports\n---\nOriginal guidance")
    (root / "references" / "style.txt").write_text("Use short sentences.")
    asyncio.run(personal.patch_skill("owner-alex", name, True))
    return root


def test_pack_is_shared_once_and_bot_edits_leave_frozen_files_and_main_alone(skill_scope):
    source = main_skill()
    left, right = make_bot(skill_scope, "Left"), make_bot(skill_scope, "Right")
    before = asyncio.run(bot_skills.discover_skills("owner-alex", left["id"]))
    assert before["items"][0]["name"] == "reports"
    with skill_scope.transaction() as db:
        assert db.get(Actor, left["id"]).bot_skill_pack_digest is None
    first = asyncio.run(bot_skills.build_skill_config("owner-alex", left["id"]))
    second = asyncio.run(bot_skills.build_skill_config("owner-alex", right["id"]))
    assert first == second
    pack = bot_skills.skill_pack_path(first["pack_digest"])
    inventory = bot_snapshot_store.read_manifest("skills", first["pack_digest"])
    for relative, entry in inventory["files"].items():
        blob = bot_snapshot_store.blob_path(entry["digest"], executable=entry["executable"])
        assert os.stat(pack / relative).st_ino == os.stat(blob).st_ino
    old_store = bot_skills.create_enabled_skill_store(first["pack_digest"], first["skills"])
    edited = asyncio.run(bot_skills.edit_skill("owner-alex", right["id"], "reports",
        "---\ndescription: Different reports\n---\nChanged only for Right"))
    assert edited["content"].startswith("---\n")
    assert "Changed only for Right" in edited["content"]
    assert asyncio.run(old_store.load("reports")).content.endswith("Original guidance")
    assert (source / "SKILL.md").read_text().endswith("Original guidance")
    current = asyncio.run(bot_skills.build_skill_config("owner-alex", right["id"]))
    assert current["pack_digest"] != first["pack_digest"]
    current_store = bot_skills.create_enabled_skill_store(current["pack_digest"], current["skills"])
    assert asyncio.run(current_store.load_reference("reports", "references/style.txt")) == "Use short sentences."
    assert asyncio.run(bot_skills.build_skill_config("owner-alex", left["id"])) == first
    (source / "SKILL.md").write_text("Later main Duet changes")
    assert asyncio.run(old_store.load("reports")).content.endswith("Original guidance")


def test_upload_selection_delete_and_freeze_only_active_skills(skill_scope):
    bot = make_bot(skill_scope)
    files = [(["planning", "SKILL.md"], b"---\ndescription: Plan work\n---\nUse the reference."),
             (["planning", "references", "plan.txt"], b"Choose a small next step.")]
    created = asyncio.run(bot_skills.upload_skill("owner-alex", bot["id"], "planning", files))
    assert created["items"] == [{"name": "planning", "description": "Plan work", "enabled": False,
                                  "available": True, "error": None}]
    with skill_scope.transaction() as db:
        actor = db.get(Actor, bot["id"])
        frozen = bot_skills.freeze_skills_db(db, actor)
        assert frozen["skills"] == [] and frozen["inventory"]["files"] == {}
    item = asyncio.run(bot_skills.patch_skill("owner-alex", bot["id"], "planning", True))
    assert item["enabled"]
    config = asyncio.run(bot_skills.build_skill_config("owner-alex", bot["id"]))
    store = bot_skills.create_enabled_skill_store(config["pack_digest"], config["skills"])
    assert asyncio.run(store.load_reference("planning", "references/plan.txt")) == "Choose a small next step."
    with pytest.raises(HTTPException) as duplicate:
        asyncio.run(bot_skills.upload_skill("owner-alex", bot["id"], "planning", files))
    assert duplicate.value.status_code == 409
    assert asyncio.run(bot_skills.delete_skill("owner-alex", bot["id"], "planning")) == {"items": []}
    assert asyncio.run(store.load("planning")).name == "planning"
    with pytest.raises(HTTPException) as missing:
        asyncio.run(bot_skills.skill_detail("owner-alex", bot["id"], "planning"))
    assert missing.value.status_code == 404
    assert not (personal.resolve_twin_workspace("owner-alex") / "skills" / "planning").exists()


def test_pack_freeze_preserves_executable_script_and_denies_foreign_or_primary_actor(skill_scope):
    source = main_skill()
    script = source / "run.sh"
    script.write_text("#!/bin/sh\nprintf 'fixture'\n")
    script.chmod(0o755)
    bot = make_bot(skill_scope)
    config = asyncio.run(bot_skills.build_skill_config("owner-alex", bot["id"]))
    assert os.access(bot_skills.skill_pack_path(config["pack_digest"]) / "reports/run.sh", os.X_OK)
    for owner, actor in [("owner-maya", bot["id"]), ("owner-alex", "twin-alex")]:
        for call in [bot_skills.discover_skills(owner, actor), bot_skills.build_skill_config(owner, actor)]:
            with pytest.raises(HTTPException) as denied:
                asyncio.run(call)
            assert denied.value.status_code == 404


@pytest.mark.parametrize("filename", [".env", ".env.production", "credentials.json", "private.key"])
def test_skill_credentials_are_rejected_without_a_pack_or_main_change(skill_scope, filename):
    bot = make_bot(skill_scope)
    with pytest.raises(HTTPException, match="credential"):
        asyncio.run(bot_skills.upload_skill("owner-alex", bot["id"], "unsafe", [
            (["unsafe", "SKILL.md"], b"---\ndescription: Fixture\n---\nFixture"),
            (["unsafe", filename], b"synthetic-secret-never-shared"),
        ]))
    with skill_scope.transaction() as db:
        assert db.get(Actor, bot["id"]).bot_skill_pack_digest is None


def test_enabled_skill_symlink_cannot_enter_a_pack(skill_scope, tmp_path):
    source = main_skill()
    outside = tmp_path / "private.txt"
    outside.write_text("synthetic-private-file")
    (source / "references" / "external.txt").symlink_to(outside)
    bot = make_bot(skill_scope)
    with pytest.raises(HTTPException, match="symbolic links"):
        asyncio.run(bot_skills.build_skill_config("owner-alex", bot["id"]))
    with skill_scope.transaction() as db:
        assert db.get(Actor, bot["id"]).bot_skill_pack_digest is None


def test_real_bot_execution_loads_its_pack_and_next_run_uses_its_own_edit(skill_scope):
    source = main_skill()
    script = source / "run.sh"
    script.write_text(
        '#!/bin/sh\nset -eu\n'
        'skill_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        'value=$(cat "$skill_directory/companion.txt")\n'
        'printf "companion=%s\\nworkspace=%s\\n" "$value" "$PWD"\n'
        'printf "%s\\n" "$value" > bot-skill-output.txt\n')
    script.chmod(0o755)
    (source / "companion.txt").write_text("Frozen companion")
    bot = make_bot(skill_scope)
    configs = {"FIRST": asyncio.run(bot_skills.build_skill_config("owner-alex", bot["id"]))}
    (source / "SKILL.md").write_text("---\ndescription: Main only\n---\nWrong global guidance")
    (source / "companion.txt").write_text("Wrong global companion")
    observed = []

    async def run():
        runtime = RuntimeBridge(skill_scope)

        def offline(model, context, _options):
            prompt = text_content(next(row for row in reversed(context.messages) if row["role"] == "user"))
            system = text_content(context.messages[0])
            marker = "This BOT's frozen Skill directory: "
            assert marker in system
            directory = Path(system.split(marker, 1)[1].split(". Each enabled Skill's scripts", 1)[0])
            assert directory == bot_skills.skill_pack_path(configs[prompt]["pack_digest"])
            assert "immutable shared snapshot assets" in system
            assert "write outputs in your workspace" in system
            results = {row["toolName"]: row for row in context.messages if row["role"] == "toolResult"}
            assert all(not result.get("isError") for result in results.values()), results
            if "load_skill" not in results:
                content = [{"type": "toolCall", "id": uuid4().hex, "name": "load_skill",
                            "arguments": {"name": "reports"}}]
            else:
                loaded = text_content(results["load_skill"])
                expected = "Original guidance" if prompt == "FIRST" else "Changed BOT guidance"
                assert expected in loaded and "Wrong global guidance" not in loaded
                if "bash" not in results:
                    content = [{"type": "toolCall", "id": uuid4().hex, "name": "bash",
                                "arguments": {"command": shlex.quote(str(directory / "reports/run.sh"))}}]
                else:
                    output = text_content(results["bash"])
                    assert "companion=Frozen companion" in output
                    assert "Wrong global companion" not in output
                    assert bot["chat_id"] in output and str(directory) not in output
                    observed.append(prompt)
                    content = [{"type": "text", "text": "Used my configured BOT Skill and script."}]
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=content,
                         stopReason="toolUse" if content[0]["type"] == "toolCall" else "stop")})
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        skill_scope.bind_runtime(runtime)
        try:
            for prompt in ("FIRST", "SECOND"):
                if prompt == "SECOND":
                    await bot_skills.edit_skill("owner-alex", bot["id"], "reports",
                        "---\ndescription: BOT only\n---\nChanged BOT guidance")
                    configs[prompt] = await bot_skills.build_skill_config("owner-alex", bot["id"])
                request = skill_scope.send_message("owner-alex", bot["chat_id"], prompt, uuid4().hex)["request"]
                await skill_scope.execute_request(request["id"])
                completed = skill_scope.get_request(request["id"])
                assert completed["status"] == "completed", completed["error"]
                frozen = personal.get_execution_config(request["id"])
                assert Path(frozen["workspace"]).name == bot["chat_id"]
                assert (Path(frozen["workspace"]) / "bot-skill-output.txt").read_text() == "Frozen companion\n"
            assert observed == ["FIRST", "SECOND"]
        finally:
            await runtime.close()

    asyncio.run(run())
