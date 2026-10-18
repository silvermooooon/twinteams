"""Temporary bare Git repositories and isolated owner configuration; no remote services."""
import asyncio
from uuid import uuid4

import pytest
from agent_runtime.memory import LocalMemoryStore, MemoryConflict, MemorySource
from agent_runtime.sessions import SessionInfo
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import personal
from app.git_memory import ManagedGitMemoryStore, migrate_store
from app.main import app
from app.service import service


def test_git_migration_preserves_sources_and_does_not_reimport_deleted_entries(tmp_path):
    async def run():
        identity = SessionInfo("migration", "test", "owner")
        legacy = LocalMemoryStore(identity, tmp_path)
        sources = (MemorySource("actual-session", "actual-event"),)
        await legacy.write("MEMORY", "Original fact", sources=sources, expected_version=None)
        files = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
        store = ManagedGitMemoryStore(identity, tmp_path)
        with pytest.raises(HTTPException, match="503"):
            await store.read("MEMORY")
        assert await migrate_store(store) == {"status": "migrated", "imported": 1}
        entry = await store.read("MEMORY")
        assert entry.content == "Original fact" and entry.sources == sources
        assert all(p.read_bytes() == value for p, value in files.items())
        await store.delete("MEMORY", expected_version=entry.version, operation_id="real-delete")
        assert await store.publication_for_operation("real-delete")
        assert await store.publication_for_operation("missing") is None
        assert await migrate_store(store) == {"status": "already_migrated", "imported": 0}
        assert await store.read("MEMORY") is None
    asyncio.run(run())


def test_git_versions_independent_keys_and_same_key_conflict(tmp_path):
    async def run():
        info = SessionInfo("s", "tenant", "user")
        left, right = ManagedGitMemoryStore(info, tmp_path), ManagedGitMemoryStore(info, tmp_path)
        source = [MemorySource("session", "event")]
        # Exercise native Git ref initialization and independent-file rebasing concurrently.
        a, b = await asyncio.gather(left.write("a", "A", sources=source, expected_version=None),
            right.write("b", "B", sources=source, expected_version=None))
        assert (await left.read("a")).content == "A" and (await right.read("b")).content == "B"
        changed = await left.write("a", "A2", sources=source, expected_version=a.version)
        with pytest.raises(MemoryConflict) as conflict:
            await right.write("a", "stale", sources=source, expected_version=a.version)
        assert conflict.value.current.version == changed.version
        assert b.version
    asyncio.run(run())


def test_soul_and_memory_http_versions_real_audit_and_owner_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_MEMORY_DIR", str(tmp_path))
    service.initialize(create_schema=True)
    client, other = TestClient(app), TestClient(app)
    assert client.post("/api/auth/login", json={"username": "DEV-AAA"}).status_code == 200
    assert other.post("/api/auth/login", json={"username": "DEV-BBB"}).status_code == 200
    current = client.get("/api/me/twin/soul").json()
    updated = client.put("/api/me/twin/soul", json={"content": "Gentle soul", "expected_version": current["version"]}).json()
    assert updated["content"] == "Gentle soul"
    conflict = client.put("/api/me/twin/soul", json={"content": "stale", "expected_version": current["version"]})
    assert conflict.status_code == 409 and conflict.json()["current"] == updated
    assert other.get("/api/me/twin/soul").json()["content"] != "Gentle soul"
    assert client.delete("/api/me/twin/soul", params={"expected_version": updated["version"]}).json()["content"] == ""
    key = "api-" + uuid4().hex
    entry = client.put("/api/me/twin/memory/entry", params={"key": key},
        json={"content": "User factual correction", "expected_version": None}).json()
    assert entry["version"] and entry["sources"][0]["session_id"] == "twin-config:owner-alex"
    ref = entry["sources"][0]
    audit = personal.get_memory_edit_source("owner-alex", **ref)
    assert audit["content"] == "User factual correction" and audit["state"] == "published"
    with pytest.raises(HTTPException):
        personal.get_memory_edit_source("owner-maya", **ref)
    collision = client.put("/api/me/twin/memory/entry", params={"key": key}, json={"content": "stale", "expected_version": None})
    assert collision.status_code == 409 and collision.json()["current"] == entry
    assert other.get("/api/me/twin/memory/entry", params={"key": key}).status_code == 404
    assert client.delete("/api/me/twin/memory/entry", params={"key": key, "expected_version": "wrong"}).status_code == 409
    assert client.delete("/api/me/twin/memory/entry", params={"key": key, "expected_version": entry["version"]}).json()["deleted"]
    assert client.get("/api/me/twin/memory/status").json()["mode"] == "git"
