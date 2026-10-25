"""Owner-scoped profile, skill discovery and versioned Git memory services."""
import hashlib
import os
import time
import warnings
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from io import BytesIO
from pathlib import Path

from agent_runtime.memory import MemoryConflict, MemorySource
from agent_runtime.memory.git import GitMemoryError, MemoryPublishUnknown
from agent_runtime.sessions import SessionInfo
from agent_runtime.skills import LocalSkillStore, SkillInfo, SkillStore
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import undefer

from .config import ROOT, settings
from .models import Actor, Chat, MemoryDreamState, MemoryEditAudit, ReplyRequest
from .notifications import notifications
from .service import fail, service

AVATAR_PRESETS = ("orbit", "bloom", "wave", "sunrise", "mountain", "spark")
MAX_AVATAR_BYTES = 5 * 1024 * 1024


def resolve_twin_workspace(owner_id):
    """Use the existing unique private chat workspace for all work by this Duet."""
    with service.transaction() as db:
        service.twin(db, owner_id)
        private_id = db.scalar(select(Chat.id).where(Chat.private_owner_id == owner_id, Chat.kind == "private"))
        if private_id is None:
            fail(409, "Owner has no private Duet workspace")
    root = settings.workspace_root
    path = root / private_id
    if not path.resolve().is_relative_to(root) or any(
        part.is_symlink() for part in [path, *path.parents] if part.is_relative_to(root)
    ):
        fail(403, "Invalid Duet workspace")
    return path


def memory_generation_guard(owner_id, generation, *, write=False):
    @contextmanager
    def guard():
        with service.transaction() as db:
            query = select(Chat).where(Chat.private_owner_id == owner_id)
            chat = db.scalar(query.with_for_update() if write else query)
            if chat and (chat.reset_pending or chat.reset_generation != generation):
                fail(409, "Memory belongs to a reset private generation")
            yield
    return guard()


def make_memory_store(identity):
    """Bind every instance to the current private generation; old writers cannot publish."""
    directory = os.getenv("AGENT_MEMORY_DIR")
    if not directory and not settings.local_identity_enabled:
        return None
    with service.transaction() as db:
        chat = db.scalar(select(Chat).where(Chat.private_owner_id == identity.user_id))
        generation = chat.reset_generation if chat else 0
        if chat and chat.reset_pending:
            fail(409, "Private reset cleanup is pending")
    from .git_memory import ManagedGitMemoryStore
    root = Path(directory or ROOT / "data" / "runtime-memory")
    if generation:
        root = root / f".reset-generation-{generation}"
    return ManagedGitMemoryStore(identity, root, generation=generation,
        generation_guard=lambda **kw: memory_generation_guard(identity.user_id, generation, **kw))


def get_soul(owner_id):
    from . import soul_file
    with service.transaction() as db:
        chat = db.scalar(select(Chat).where(Chat.private_owner_id == owner_id).with_for_update())
        twin = service.twin(db, owner_id)
        workspace = settings.workspace_root / chat.id
        soul_file.initialize(workspace, twin.name)
        return soul_file.read(workspace)


def write_soul(owner_id, content, expected_version, *, delete=False):
    from . import soul_file
    with service.transaction() as db:
        chat = db.scalar(select(Chat).where(Chat.private_owner_id == owner_id).with_for_update())
        workspace = settings.workspace_root / chat.id
        soul_file.initialize(workspace, service.twin(db, owner_id).name)
        current = soul_file.read(workspace)
        if current["version"] != expected_version:
            return {"conflict": True, "current": current}
        if delete:
            (workspace / "SOUL.md").unlink(missing_ok=True)
            soul_file.sync_directory(workspace)
        else:
            soul_file.atomic_write(workspace / "SOUL.md", content)
        result = soul_file.read(workspace)
    notifications.emit_personal(owner_id, "soul")
    return result


def _memory_audit(owner_id, key, content, action, generation=None):
    with service.transaction() as db:
        service.twin(db, owner_id)
        chat = db.scalar(select(Chat).where(Chat.private_owner_id == owner_id).with_for_update())
        if chat.reset_pending or (generation is not None and chat.reset_generation != generation):
            fail(409, "Memory edit belongs to a reset private generation")
        row = MemoryEditAudit(owner_id=owner_id, key=key, content=content, action=action, private_generation=chat.reset_generation)
        db.add(row)
        db.flush()
        return row.id


def _memory_audit_finished(audit_id, state, version=None):
    with service.transaction() as db:
        row = db.get(MemoryEditAudit, audit_id)
        chat = db.scalar(select(Chat).where(Chat.private_owner_id == row.owner_id))
        if row.private_generation == chat.reset_generation and not chat.reset_pending:
            row.state, row.version = state, version


def get_memory_edit_source(owner_id, session_id, event_id):
    if session_id != f"twin-config:{owner_id}":
        fail(403, "Configuration source belongs to another owner")
    with service.transaction() as db:
        row = db.get(MemoryEditAudit, event_id)
        chat = db.scalar(select(Chat).where(Chat.private_owner_id == owner_id))
        if not row or row.owner_id != owner_id or row.private_generation != chat.reset_generation or chat.reset_pending:
            fail(404, "Memory configuration source is unavailable")
        return {"action": row.action, "key": row.key, "content": row.content,
                "created_at": row.created_at.isoformat(), "state": row.state}


async def edit_memory(owner_id, key, *, content=None, expected_version=None, delete=False):
    store = await service.db_call(own_memory_store, owner_id)
    if store is None:
        fail(503, "Memory is disabled")
    from agent_runtime.memory.store import validate_key
    try:
        validate_key(key)
    except ValueError:
        fail(422, "Memory key is invalid")
    audit_id = await service.db_call(_memory_audit, owner_id, key, content, "delete" if delete else "write", store.generation)
    operation_id = "configuration:" + audit_id
    try:
        if delete:
            await store.delete(key, expected_version=expected_version, operation_id=operation_id)
            response, version = {"deleted": True, "key": key}, None
        else:
            entry = await store.write(key, content, expected_version=expected_version, operation_id=operation_id,
                sources=[MemorySource(f"twin-config:{owner_id}", audit_id)])
            response, version = entry.to_dict(), entry.version
    except MemoryConflict as conflict:
        await service.db_call(_memory_audit_finished, audit_id, "conflict")
        return {"conflict": True, "current": conflict.current.to_dict() if conflict.current else None}
    except MemoryPublishUnknown:
        await service.db_call(_memory_audit_finished, audit_id, "recovery_required")
        fail(503, "Memory publication could not be confirmed; reload and reconcile before retrying")
    except GitMemoryError:
        await service.db_call(_memory_audit_finished, audit_id, "failed")
        fail(503, "Git memory is unavailable")
    except Exception:
        await service.db_call(_memory_audit_finished, audit_id, "failed")
        raise
    await service.db_call(_memory_audit_finished, audit_id, "published", version)
    notifications.emit_personal(owner_id, "memory")
    return response


def memory_status(owner_id):
    enabled = own_memory_store(owner_id) is not None
    with service.transaction() as db:
        row = db.get(MemoryDreamState, owner_id)
        dream = {"status": row.last_status if row else "idle", "due_at": row.due_at.isoformat() if row and row.due_at else None,
            "cursor_seq": row.cursor_seq if row else 0,
            "message_cursor_seq": row.message_cursor_seq if row else None, "last_version": row.last_version if row else None,
            "last_success_at": row.last_success_at.isoformat() if row and row.last_success_at else None,
            "last_error": row.last_error if row else None}
    return {"enabled": enabled, "mode": "git" if enabled else "disabled", "dream": dream}


def own_memory_store(owner_id):
    with service.transaction() as db:
        service.twin(db, owner_id)
    return make_memory_store(SessionInfo("personal-memory", settings.tenant_id, owner_id))


def get_twin(owner_id):
    with service.transaction() as db:
        return {"actor": service.actor_json(service.twin(db, owner_id))}


def get_actor(owner_id, actor_id):
    # Actor profile metadata is already visible in authenticated bootstrap/people.
    with service.transaction() as db:
        actor = db.get(Actor, actor_id)
        if not actor:
            fail(404, "Actor not found")
        return service.actor_json(actor)


def patch_twin(owner_id, values):
    with service.transaction() as db:
        actor = db.scalar(select(Actor).where(Actor.owner_id == owner_id, Actor.kind == "twin").with_for_update())
        if actor is None:
            fail(409, "Owner has no paired Duet")
        changed = False
        if "name" in values:
            name = values["name"].strip()
            if not name or len(name) > 128 or any(ord(c) < 32 for c in name):
                fail(422, "Duet name must contain 1 to 128 visible characters")
            if actor.name != name:
                actor.name = name
                actor.initials = "".join(word[0] for word in name.split()[:2]).upper()[:2]
                changed = True
        if "avatar_preset" in values:
            preset = values["avatar_preset"]
            if preset is not None and preset not in AVATAR_PRESETS:
                fail(422, "Unknown avatar preset")
            if actor.avatar_preset != preset or actor.avatar_digest is not None:
                actor.avatar_preset, actor.avatar_data, actor.avatar_digest = preset, None, None
                changed = True
        if changed:
            actor.profile_revision += 1
        result = service.actor_json(actor)
    if changed:
        if "name" in values:
            from . import soul_file
            workspace = resolve_twin_workspace(owner_id)
            soul_file.initialize(workspace, result["name"])
            soul_file.rename(workspace, result["name"])
        notifications.emit_actor(result["id"], result["profile_revision"])
        notifications.emit_personal(owner_id, "profile")
    return result


def normalize_avatar(data):
    if not data or len(data) > MAX_AVATAR_BYTES:
        fail(413, "Avatar must be at most 5 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as original:
                if getattr(original, "is_animated", False):
                    fail(422, "Avatar must be a static image")
                if original.format not in {"PNG", "JPEG", "WEBP"}:
                    fail(422, "Avatar must be a PNG, JPEG or WebP image")
                if original.width * original.height > 20_000_000 or original.width < 1 or original.height < 1:
                    fail(422, "Avatar dimensions are too large")
                image = ImageOps.exif_transpose(original).convert("RGBA")
                image = ImageOps.fit(image, (256, 256), method=Image.Resampling.LANCZOS)
                output = BytesIO()
                image.save(output, format="WEBP", quality=88, method=4)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        fail(422, "Avatar image could not be decoded")


def set_avatar(owner_id, data):
    normalized = normalize_avatar(data) if data is not None else None
    with service.transaction() as db:
        actor = db.scalar(select(Actor).where(Actor.owner_id == owner_id, Actor.kind == "twin").with_for_update())
        if actor is None:
            fail(409, "Owner has no paired Duet")
        actor.avatar_data = normalized
        actor.avatar_digest = hashlib.sha256(normalized).hexdigest() if normalized else None
        actor.avatar_preset = None
        actor.profile_revision += 1
        result = service.actor_json(actor)
    notifications.emit_actor(result["id"], result["profile_revision"])
    notifications.emit_personal(owner_id, "profile")
    return result


def avatar_file(owner_id, actor_id, etag=None):
    with service.transaction() as db:
        actor = db.get(Actor, actor_id)
        if actor is None or actor.avatar_digest is None:
            fail(404, "Actor avatar not found")
        current = '"' + actor.avatar_digest + '"'
        if etag == current:
            return None, current
        # Only this dedicated route opts in to the deferred binary column.
        actor = db.scalar(select(Actor).options(undefer(Actor.avatar_data)).where(Actor.id == actor_id))
        return actor.avatar_data, current


def _skill_root(owner_id):
    workspace = resolve_twin_workspace(owner_id)
    root = workspace / "skills"
    if root.is_symlink() or not root.resolve().is_relative_to(workspace.resolve()):
        fail(403, "Invalid Skills directory")
    return root


def _skill_state(owner_id):
    root = _skill_root(owner_id)
    with service.transaction() as db:
        enabled = set(service.twin(db, owner_id).enabled_skill_names or [])
    return root, enabled


async def discover_skills(owner_id):
    root, enabled = await service.db_call(_skill_state, owner_id)
    if not root.exists():
        return {"items": [{"name": name, "description": "", "enabled": True, "available": False,
                           "error": "Skill file is missing"} for name in sorted(enabled)]}
    if not root.is_dir():
        fail(503, "Skills directory is unavailable")
    store = LocalSkillStore(root, env={})
    def candidates():
        return sorted(child.name for child in root.iterdir()
                      if not child.name.startswith(".") and child.is_dir() and (child / "SKILL.md").is_file())
    names = sorted(set(await service.db_call(candidates)) | enabled)
    result = []
    for name in names:
        try:
            info = await store.describe(name)
            result.append({**asdict(info), "enabled": name in enabled, "available": True, "error": None})
        except (OSError, ValueError, UnicodeError):
            result.append({"name": name, "description": "", "enabled": name in enabled,
                           "available": False, "error": "Skill metadata is invalid or unavailable"})
    return {"items": result}


def _validate_skill_name(name):
    if not name or name.startswith(".") or Path(name).name != name or "\\" in name:
        fail(422, "Skill name must be a visible directory name")


async def skill_detail(owner_id, name):
    _validate_skill_name(name)
    root = await service.db_call(_skill_root, owner_id)
    try:
        skill = await LocalSkillStore(root, env={}).load(name)
    except FileNotFoundError:
        fail(404, "Skill not found")
    except (OSError, ValueError, UnicodeError):
        fail(422, "Skill is invalid or unavailable")
    return asdict(skill)


async def patch_skill(owner_id, name, enabled):
    _validate_skill_name(name)
    available, error = True, None
    root = await service.db_call(_skill_root, owner_id)
    try:
        info = await LocalSkillStore(root, env={}).describe(name)
    except FileNotFoundError:
        if enabled:
            fail(404, "Skill not found")
        info, available, error = SkillInfo(name, ""), False, "Skill file is missing"
    except (OSError, ValueError, UnicodeError):
        if enabled:
            fail(422, "Invalid Skills cannot be enabled")
        info, available, error = SkillInfo(name, ""), False, "Skill metadata is invalid or unavailable"
    def save():
        with service.transaction() as db:
            actor = db.scalar(select(Actor).where(Actor.owner_id == owner_id, Actor.kind == "twin").with_for_update())
            current = set(actor.enabled_skill_names or [])
            if enabled:
                current.add(name)
            else:
                current.discard(name)
            actor.enabled_skill_names = sorted(current)
    await service.db_call(save)
    notifications.emit_personal(owner_id, "skills")
    return {**asdict(info), "enabled": enabled, "available": available, "error": error}


async def build_skill_config(owner_id):
    catalog = await discover_skills(owner_id)
    return [{"name": item["name"], "description": item["description"]}
            for item in catalog["items"] if item["enabled"] and item["available"]]


class EnabledSkillStore(SkillStore):
    """A task's frozen catalog authorizes both dedicated load operations."""
    def __init__(self, directory, skills):
        self.directory = Path(directory)
        self.catalog = tuple(SkillInfo(item["name"], item["description"]) for item in skills)
        self.allowed = frozenset(item.name for item in self.catalog)

    def _store(self, name):
        if name not in self.allowed:
            raise PermissionError("Skill is not enabled for this task")
        if self.directory.is_symlink():
            raise PermissionError("Invalid Skills directory")
        return LocalSkillStore(self.directory, env={})

    async def discover(self):
        return list(self.catalog)

    async def load(self, name):
        return await self._store(name).load(name)

    async def load_reference(self, name, reference):
        store = self._store(name)
        await store.describe(name)  # A now-invalid skill cannot bypass checks through references.
        return await store.load_reference(name, reference)


def create_enabled_skill_store(owner_id, skills_snapshot):
    return EnabledSkillStore(_skill_root(owner_id), skills_snapshot)


def get_execution_config(request_id):
    with service.transaction() as db:
        request = db.get(ReplyRequest, request_id)
        if request is None:
            fail(404, "Reply request not found")
        return deepcopy(request.execution_config)


def freeze_execution_config(request_id, config):
    # Only metadata belongs here; fail closed if a caller accidentally supplies values.
    for item in config.get("credentials", []):
        if set(item) - {"ref", "description", "env_names"}:
            raise ValueError("Execution configuration accepts credential metadata only")
    with service.transaction() as db:
        request = db.get(ReplyRequest, request_id)
        if request is None:
            fail(404, "Reply request not found")
        db.scalar(select(Chat).where(Chat.id == request.chat_id).with_for_update())
        db.refresh(request)
        if request.execution_config is None:
            from .scheduled_tasks import snapshot
            task = snapshot(request)
            request.execution_config = {**deepcopy(config), **({"scheduled_task": task} if task else {})}
        return deepcopy(request.execution_config)


def runtime_status(owner_id):
    status = notifications.worker_status
    online = status.get("expires_at", 0) > time.time()
    with service.transaction() as db:
        service.twin(db, owner_id)
    return {"configured": bool(online and status.get("configured")),
            **{name: status.get(name) for name in ("provider", "model", "api", "observed_at")},
            "memory": status.get("memory") or {"enabled": None, "mode": None},
            "worker": {"online": online, "expires_at": status.get("expires_at")}}
