"""Personal BOT skill packs: immutable files, independent per-BOT selection."""
import asyncio
import errno
import os
import stat
import tempfile
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from agent_runtime.skills import LocalSkillStore

from . import personal
from .bot_snapshot_store import blob_path, put_blob, read_manifest, storage_root, write_manifest
from .notifications import notifications
from .personal_bots import require_bot
from .service import fail


async def discover_skills(owner_id, bot_id):
    return await personal.service.db_call(_discover_skills, owner_id, bot_id)


async def skill_detail(owner_id, bot_id, name):
    personal._validate_skill_name(name)
    return await personal.service.db_call(_skill_detail, owner_id, bot_id, name)


async def build_skill_config(owner_id, bot_id):
    return await personal.service.db_call(_build_skill_config, owner_id, bot_id)


def create_enabled_skill_store(pack_digest, skills):
    return personal.EnabledSkillStore(skill_pack_path(pack_digest), skills)


async def upload_skill(owner_id, bot_id, name, files):
    return await personal.service.db_call(_upload_skill, owner_id, bot_id, name, files)


async def patch_skill(owner_id, bot_id, name, enabled):
    personal._validate_skill_name(name)
    return await personal.service.db_call(_patch_skill, owner_id, bot_id, name, enabled)


async def delete_skill(owner_id, bot_id, name):
    personal._validate_skill_name(name)
    return await personal.service.db_call(_delete_skill, owner_id, bot_id, name)


async def edit_skill(owner_id, bot_id, name, content):
    personal._validate_skill_name(name)
    return await personal.service.db_call(_edit_skill, owner_id, bot_id, name, content)


def freeze_skills_db(db, actor):
    """The caller holds the BOT actor lock while freezing its other configuration."""
    inventory = _seed(db, actor)
    enabled = set(actor.enabled_skill_names or [])
    selected = {"files": {path: entry for path, entry in inventory["files"].items()
                          if path.split("/", 1)[0] in enabled},
                "skills": [item for item in inventory["skills"] if item["name"] in enabled],
                "enabled": sorted(enabled)}
    pack_digest = _publish_pack(selected)
    return {"pack_digest": pack_digest, "skills": selected["skills"],
            "enabled": selected["enabled"], "inventory": selected}


def skill_pack_path(pack_digest):
    return storage_root() / "skills" / pack_digest


def _build_skill_config(owner_id, bot_id):
    with personal.service.transaction() as db:
        actor = _locked_actor(db, owner_id, bot_id)
        inventory = _seed(db, actor)
        enabled = set(actor.enabled_skill_names or [])
        return {"pack_digest": actor.bot_skill_pack_digest,
                "skills": [item for item in inventory["skills"] if item["name"] in enabled]}


def _discover_skills(owner_id, bot_id):
    with personal.service.transaction() as db:
        actor, _, _ = require_bot(db, owner_id, bot_id)
        if actor.bot_skill_pack_digest:
            return _catalog(read_manifest("skills", actor.bot_skill_pack_digest),
                            actor.enabled_skill_names or [])
    # Before its first execution, the BOT starts with the main Duet's active catalog.
    # This read neither captures files nor changes either actor's configuration.
    return {"items": [{**item, "enabled": True} for item in _initial_catalog(owner_id)]}


def _skill_detail(owner_id, bot_id, name):
    with personal.service.transaction() as db:
        actor, _, _ = require_bot(db, owner_id, bot_id)
        if actor.bot_skill_pack_digest:
            inventory = read_manifest("skills", actor.bot_skill_pack_digest)
            _require_skill(inventory, name)
            root = skill_pack_path(actor.bot_skill_pack_digest)
        else:
            if name not in {item["name"] for item in _initial_catalog(owner_id)}:
                fail(404, "Skill not found in this BOT")
            root = personal._skill_root(owner_id)
        return _detail(root, name)


def _upload_skill(owner_id, bot_id, name, files):
    # Folder paths and total bytes have already been checked by the upload boundary.
    for parts, _ in files:
        check_shareable_path(parts)
    with tempfile.TemporaryDirectory(prefix="duet-bot-skill-") as temporary:
        root = Path(temporary)
        for parts, data in files:
            target = root.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        info = _describe(root, name)
        additions = _capture_files(root, name)
    with personal.service.transaction() as db:
        actor = _locked_actor(db, owner_id, bot_id)
        inventory = _seed(db, actor)
        if any(item["name"] == name for item in inventory["skills"]):
            fail(409, "A Skill with this folder name already exists in this BOT")
        inventory["skills"].append(info)
        inventory["files"].update(additions)
        _save(actor, inventory)
        result = _catalog(inventory, actor.enabled_skill_names)
    notifications.emit_personal(owner_id, "bots")
    return result


def _patch_skill(owner_id, bot_id, name, enabled):
    with personal.service.transaction() as db:
        actor = _locked_actor(db, owner_id, bot_id)
        inventory = _seed(db, actor)
        info = _require_skill(inventory, name)
        names = set(actor.enabled_skill_names or [])
        names.add(name) if enabled else names.discard(name)
        inventory["enabled"] = sorted(names)
        _save(actor, inventory)
    notifications.emit_personal(owner_id, "bots")
    return {**info, "enabled": enabled, "available": True, "error": None}


def _delete_skill(owner_id, bot_id, name):
    with personal.service.transaction() as db:
        actor = _locked_actor(db, owner_id, bot_id)
        inventory = _seed(db, actor)
        _require_skill(inventory, name)
        inventory["skills"] = [item for item in inventory["skills"] if item["name"] != name]
        inventory["files"] = {path: entry for path, entry in inventory["files"].items()
                              if path.split("/", 1)[0] != name}
        inventory["enabled"] = [item for item in inventory["enabled"] if item != name]
        _save(actor, inventory)
        result = _catalog(inventory, actor.enabled_skill_names)
    notifications.emit_personal(owner_id, "bots")
    return result


def _edit_skill(owner_id, bot_id, name, content):
    data = content.encode("utf-8")
    if len(data) > 20 * 1024 * 1024:
        fail(413, "Skill files are limited to 20 MiB")
    with tempfile.TemporaryDirectory(prefix="duet-bot-skill-edit-") as temporary:
        root = Path(temporary)
        (root / name).mkdir()
        (root / name / "SKILL.md").write_bytes(data)
        info = _describe(root, name)
    with personal.service.transaction() as db:
        actor = _locked_actor(db, owner_id, bot_id)
        inventory = _seed(db, actor)
        _require_skill(inventory, name)
        inventory["skills"] = [info if item["name"] == name else item
                               for item in inventory["skills"]]
        inventory["files"][f"{name}/SKILL.md"] = {
            "digest": put_blob(data), "size": len(data), "executable": False}
        _save(actor, inventory)
        result = _detail(skill_pack_path(actor.bot_skill_pack_digest), name)
    notifications.emit_personal(owner_id, "bots")
    return result


def _locked_actor(db, owner_id, bot_id):
    actor, _, _ = require_bot(db, owner_id, bot_id)
    db.refresh(actor, with_for_update=True)
    return actor


def _seed(db, actor):
    if actor.bot_skill_pack_digest:
        return deepcopy(read_manifest("skills", actor.bot_skill_pack_digest))
    root = personal._skill_root(actor.owner_id)
    catalog = _initial_catalog(actor.owner_id)
    inventory = {"files": {}, "skills": [], "enabled": []}
    for item in catalog:
        if not item["available"]:
            continue
        inventory["files"].update(_capture_files(root, item["name"]))
        inventory["skills"].append({"name": item["name"], "description": item["description"]})
        inventory["enabled"].append(item["name"])
    _save(actor, inventory)
    return inventory


def _initial_catalog(owner_id):
    root, enabled = personal._skill_state(owner_id)
    result = []
    for name in sorted(enabled):
        personal._validate_skill_name(name)
        try:
            info = asdict(asyncio.run(LocalSkillStore(root, env={}).describe(name)))
            result.append({**info, "enabled": True, "available": True, "error": None})
        except (OSError, ValueError, UnicodeError):
            result.append({"name": name, "description": "", "enabled": True,
                           "available": False, "error": "Skill metadata is invalid or unavailable"})
    return result


def _save(actor, inventory):
    inventory["skills"] = sorted(inventory["skills"], key=lambda item: item["name"])
    inventory["enabled"] = sorted(inventory["enabled"])
    actor.bot_skill_pack_digest = _publish_pack(inventory)
    actor.enabled_skill_names = inventory["enabled"]


def _publish_pack(inventory):
    digest = write_manifest("skills", inventory)
    destination = skill_pack_path(digest)
    if destination.is_dir():
        return digest
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pack-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "skills"
        staging.mkdir()
        for relative, entry in sorted(inventory["files"].items()):
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            os.link(blob_path(entry["digest"], executable=entry["executable"]), path)
        try:
            staging.rename(destination)
        except OSError as exc:
            if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY} or not destination.is_dir():
                raise
        for directory, _, _ in os.walk(destination, topdown=False):
            Path(directory).chmod(0o555)
    return digest


def _capture_files(root, name):
    folder = root / name
    if folder.is_symlink() or not folder.resolve().is_relative_to(root.resolve()):
        fail(422, "Shared Skills cannot contain symbolic links")
    files = {}
    for directory, directories, names in os.walk(folder, followlinks=False):
        for child in sorted([*directories, *names]):
            path = Path(directory) / child
            relative = path.relative_to(root)
            check_shareable_path(relative.parts)
            if path.is_symlink():
                fail(422, "Shared Skills cannot contain symbolic links")
            if child not in names:
                continue
            mode = path.stat().st_mode
            if not stat.S_ISREG(mode):
                fail(422, "Shared Skills must contain ordinary files")
            data = path.read_bytes()
            executable = bool(mode & 0o111)
            files[relative.as_posix()] = {
                "digest": put_blob(data, executable=executable),
                "size": len(data), "executable": executable}
    return files


def check_shareable_path(parts):
    if any(part.lower() in {".git", ".ssh", ".aws", "credentials", "credentials.json",
                           "secrets", "secrets.json", "token.json", "tokens.json",
                           "id_rsa", "id_ed25519"}
           or part.lower().startswith(".env") or part.lower().endswith((".pem", ".key", ".p12", ".pfx"))
           for part in parts):
        fail(422, "Remove credential and login files from the Skill before sharing it")


def _describe(root, name):
    try:
        skill = asyncio.run(LocalSkillStore(root, env={}).load(name))
        return {"name": skill.name, "description": skill.description}
    except (OSError, ValueError, UnicodeError):
        fail(422, "Skill is invalid or unavailable")


def _detail(root, name):
    try:
        return asdict(asyncio.run(LocalSkillStore(root, env={}).load(name)))
    except FileNotFoundError:
        fail(404, "Skill not found in this BOT")
    except (OSError, ValueError, UnicodeError):
        fail(422, "Skill is invalid or unavailable")


def _require_skill(inventory, name):
    info = next((item for item in inventory["skills"] if item["name"] == name), None)
    if info is None:
        fail(404, "Skill not found in this BOT")
    return info


def _catalog(inventory, enabled):
    return {"items": [{**info, "enabled": info["name"] in enabled,
                        "available": True, "error": None} for info in inventory["skills"]]}
