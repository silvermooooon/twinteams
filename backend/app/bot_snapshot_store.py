"""Immutable BOT assets and shared Git objects; installed repos write their own objects."""
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from agent_runtime.sessions import SessionInfo

from .config import ROOT, settings
from .git_memory import ManagedGitMemoryStore
from .service import fail

RULE_FILES = ("SOUL.md", "AGENTS.md", "RULES.md", "rules.md")


def storage_root():
    return settings.workspace_root / ".bot-share"


def blob_path(digest, *, executable=False):
    return storage_root() / "blobs" / (digest + (".x" if executable else ""))


def put_blob(data, *, executable=False):
    digest = hashlib.sha256(data).hexdigest()
    _publish_file(blob_path(digest, executable=executable), data, 0o555 if executable else 0o444)
    return digest


def write_manifest(kind, inventory):
    data = json.dumps(inventory, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    digest = hashlib.sha256(data).hexdigest()
    _publish_file(storage_root() / "manifests" / kind / (digest + ".json"), data, 0o444)
    return digest


def read_manifest(kind, digest):
    return json.loads((storage_root() / "manifests" / kind / (digest + ".json")).read_bytes())


def _publish_file(path, data, mode):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent) as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
        os.chmod(stream.name, mode)
        try:
            os.link(stream.name, path)
        except FileExistsError:
            pass  # Another publisher saved the same content-addressed object.


def memory_store(owner_id, bot_id):
    root = Path(os.getenv("AGENT_MEMORY_DIR") or ROOT / "data" / "runtime-memory")
    return ManagedGitMemoryStore(SessionInfo("bot-snapshot", settings.tenant_id, owner_id),
                                 root / "bots" / bot_id)


def freeze_memory(owner_id, bot_id, snapshot_id):
    if not os.getenv("AGENT_MEMORY_DIR") and not settings.local_identity_enabled:
        return None, False
    store = memory_store(owner_id, bot_id)
    store._initialize()
    source = Path(store.remote)
    head = store._git(source, "rev-parse", "refs/heads/main").stdout.decode().strip()
    tree = store._git(source, "rev-parse", head + "^{tree}").stdout.decode().strip()
    # Only the current tree travels. Deleted Memory versions and source commit
    # history are not part of a current-state share.
    commit_body = (f"tree {tree}\nauthor Duet <snapshot@duet.local> 0 +0000\n"
                   "committer Duet <snapshot@duet.local> 0 +0000\n\nBOT Memory snapshot\n").encode()
    commit = store._git(source, "hash-object", "-w", "-t", "commit", "--stdin",
                        input_bytes=commit_body).stdout.decode().strip()
    ref = "refs/bot-snapshots/" + snapshot_id
    store._git(source, "update-ref", ref, commit, "0" * 40)
    shared = shared_memory_repo(store)
    store._git(shared, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head",
               str(source), f"{ref}:{ref}")
    included = bool(store._git(source, "ls-tree", "-r", "--name-only", tree).stdout.strip())
    return commit, included


def shared_memory_repo(store):
    root = storage_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    remote = root / "memory.git"
    if not remote.exists():
        with tempfile.TemporaryDirectory(prefix=".memory-init-", dir=root) as temporary:
            prepared = Path(temporary) / "repo"
            store._git(None, "init", "--quiet", "--bare", "--initial-branch=main", str(prepared))
            try:
                os.rename(prepared, remote)
            except OSError:
                if not (remote / "HEAD").exists():
                    raise
    return remote


def install_memory(owner_id, bot_id, commit):
    if commit is None:
        return
    store = memory_store(owner_id, bot_id)
    store.scope.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=".snapshot-install-", dir=store.scope) as temporary:
        prepared = Path(temporary) / "repo"
        store._git(None, "init", "--quiet", "--bare", "--initial-branch=main", str(prepared))
        shared = storage_root() / "memory.git" / "objects"
        (prepared / "objects" / "info" / "alternates").write_text(str(shared.resolve()) + "\n")
        store._git(prepared, "update-ref", "refs/heads/main", commit, "0" * 40)
        os.rename(prepared, store.remote)
    store._mark()


def rule_paths(workspace):
    from .bot_skills import check_shareable_path
    if workspace.is_symlink():
        fail(422, "BOT rule workspace cannot be a symbolic link")
    paths = [workspace / name for name in RULE_FILES if (workspace / name).exists()
             or (workspace / name).is_symlink()]
    directory = workspace / "rules"
    if directory.is_symlink():
        fail(422, "BOT rules cannot contain symbolic links")
    if directory.exists():
        paths.extend(path for path in sorted(directory.rglob("*")) if not path.is_dir() or path.is_symlink())
    for path in paths:
        check_shareable_path(path.relative_to(workspace).parts)
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".json", ".yaml", ".yml"}:
            fail(422, "BOT rules must be regular text files")
    return paths


def freeze_rules(workspace):
    files = {}
    for path in rule_paths(workspace):
        data = path.read_bytes()
        try:
            data.decode("utf-8")
        except UnicodeError:
            fail(422, "BOT rules must be UTF-8 text")
        files[path.relative_to(workspace).as_posix()] = {"digest": put_blob(data), "size": len(data)}
    return write_manifest("rules", {"files": files})


def install_rules(workspace, digest):
    # Rule files are small editable workspace inputs. Unlike shared Skill packs,
    # these are private copies so normal shell edits cannot mutate a snapshot.
    for name, item in read_manifest("rules", digest)["files"].items():
        destination = workspace / name
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(blob_path(item["digest"]), destination)


def read_rules(workspace):
    parts = []
    for path in rule_paths(workspace):
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeError:
            fail(422, "BOT rules must be UTF-8 text")
        parts.append(f"{path.relative_to(workspace).as_posix()}\n{content}")
    return "\n\n".join(parts)
