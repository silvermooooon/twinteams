"""User-scoped local Git memory, explicit JSON migration and native ref CAS initialization."""
import asyncio
import json
import os
import re
import tempfile
from contextlib import nullcontext
from pathlib import Path

from agent_runtime.memory import GitMemoryStore, LocalMemoryStore
from agent_runtime.memory.store import MemoryConflict
from agent_runtime.tools.operations import settle
from fastapi import HTTPException


class ManagedGitMemoryStore(GitMemoryStore):
    def __init__(self, identity, directory, *, generation=0, generation_guard=None):
        self.generation, self.generation_guard = generation, generation_guard
        self.legacy = LocalMemoryStore(identity, directory)
        self.scope = self.legacy.directory
        self.marker = self.scope / ".git-memory-migrated.json"
        super().__init__(identity, self.scope / "git-memory.git")

    def _mark(self):
        self.scope.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=self.scope, delete=False, mode="w") as stream:
            json.dump({"format": 1, "backend": "git"}, stream)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = stream.name
        os.replace(temporary, self.marker)

    def _guard(self, write=False):
        return self.generation_guard(write=write) if self.generation_guard else nullcontext()

    def _initialize(self, *, migration=False):
        if self.marker.exists() and Path(self.remote).exists():
            with self._guard():
                return
        with self._guard(write=True):
            return self._initialize_locked(migration=migration)

    def _initialize_locked(self, *, migration=False):
        if not self.marker.exists() and not migration:
            legacy = [path for path in self.scope.rglob("*.json")
                      if not any(part.startswith(".") for part in path.relative_to(self.scope).parts)
                      and "git-memory.git" not in path.relative_to(self.scope).parts] if self.scope.exists() else []
            if legacy:
                raise HTTPException(503, "Legacy memory requires explicit Git migration")
        remote = Path(self.remote)
        self.scope.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not remote.exists():
            # Publish a fully initialized directory atomically; concurrent Git init
            # in the same directory races on Git's own config.lock. No host lock.
            with tempfile.TemporaryDirectory(dir=self.scope, prefix=".git-init-") as temporary:
                prepared = Path(temporary) / "repo"
                self._git(None, "init", "--quiet", "--bare", "--initial-branch=main", str(prepared))
                tree = self._git(prepared, "hash-object", "-w", "-t", "tree", "/dev/null").stdout.decode().strip()
                commit = self._git(prepared, "commit-tree", tree, "-m", "Initialize Twin memory").stdout.decode().strip()
                self._git(prepared, "update-ref", "refs/heads/main", commit, "0" * 40)
                try:
                    os.rename(prepared, remote)
                except OSError:
                    if not (remote / "HEAD").exists():
                        raise
        exists = self._git(remote, "rev-parse", "--verify", "refs/heads/main", check=False)
        if exists.returncode:
            tree = self._git(remote, "hash-object", "-w", "-t", "tree", "/dev/null").stdout.decode().strip()
            commit = self._git(remote, "commit-tree", tree, "-m", "Initialize Twin memory").stdout.decode().strip()
            # Native Git reference compare-and-swap chooses one initializer.
            self._git(remote, "update-ref", "refs/heads/main", commit, "0" * 40, check=False)
            self._git(remote, "rev-parse", "--verify", "refs/heads/main")
        if not migration and not self.marker.exists():
            self._mark()

    def _clone(self, directory):
        self._initialize()
        result = super()._clone(directory)
        with self._guard():
            return result

    def _publish(self, directory, changes, expected, operation_id):
        with self._guard(write=True):
            return super()._publish(directory, changes, expected, operation_id)

    async def read(self, key):
        result = await super().read(key)
        with self._guard():
            return result

    async def search(self, query, limit=10):
        result = await super().search(query, limit=limit)
        with self._guard():
            return result

    async def list_entries(self, *, limit=20, after=None):
        result = await super().list_entries(limit=limit, after=after)
        with self._guard():
            return result

    def mark_migration(self):
        with self._guard(write=True):
            self._mark()

    async def publication_for_operation(self, operation_id):
        if not isinstance(operation_id, str) or not operation_id or "\n" in operation_id:
            raise ValueError("Invalid memory operation reference")
        def probe():
            self._initialize()
            record = self._git(Path(self.remote), "log", "refs/heads/main", "--extended-regexp",
                "--grep=^Memory-Operation: " + re.escape(operation_id) + "$", "--format=%H%x00%B", "-1").stdout.decode()
            if "\0" in record:
                version, message = record.split("\0", 1)
                if f"Memory-Operation: {operation_id}" in message.splitlines():
                    return version.strip()
            return None
        return await settle(asyncio.to_thread(probe))


async def migrate_store(store):
    """Only explicit provisioning imports legacy values; completed imports never repeat."""
    if store.marker.exists():
        return {"status": "already_migrated", "imported": 0}
    await settle(asyncio.to_thread(store._initialize, migration=True))
    # During the explicit import bypass only the uncompleted legacy guard.
    ordinary_clone = store._clone
    store._clone = lambda directory: GitMemoryStore._clone(store, directory)
    imported, cursor = 0, None
    try:
        while True:
            page = await store.legacy.list_entries(limit=100, after=cursor)
            for entry in page["items"]:
                current = await store.read(entry.key)
                if current:
                    if current.content != entry.content or not set(entry.sources).issubset(current.sources):
                        raise MemoryConflict(entry.key, current)
                else:
                    await store.write(entry.key, entry.content, sources=entry.sources,
                        expected_version=None, operation_id="legacy-import:" + entry.key)
                    imported += 1
            cursor = page["next_cursor"]
            if cursor is None:
                break
        await settle(asyncio.to_thread(store.mark_migration))
    finally:
        store._clone = ordinary_clone
    return {"status": "migrated", "imported": imported}
