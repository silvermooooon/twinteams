"""SOUL.md canonical bytes, deletion marker, managed name and one-way DB migration."""
from dataclasses import replace

from sqlalchemy import inspect, text
from test_memory_dream import product as product  # noqa: PLC0414

from app import config, personal, personal_schema
from app import service as service_module


def test_soul_file_agent_edit_hash_conflict_delete_and_managed_name(product, monkeypatch, tmp_path):
    configured = replace(personal.settings, workspace_root=tmp_path / "workspace")
    monkeypatch.setattr(personal, "settings", configured)
    first = personal.get_soul("owner-alex")
    assert "You're not a chatbot" in first["content"] and "SOUL.md — AAA" in first["content"]
    assert isinstance(first["version"], str)
    path = personal.resolve_twin_workspace("owner-alex") / "SOUL.md"
    path.write_text("Agent changed persona without managed markers.")
    changed = personal.get_soul("owner-alex")
    assert changed["version"] != first["version"]
    assert personal.write_soul("owner-alex", "stale form", first["version"])["conflict"]
    personal.patch_twin("owner-alex", {"name": "Nova"})
    renamed = personal.get_soul("owner-alex")
    assert "SOUL.md — Nova" in renamed["content"] and "Agent changed persona" in renamed["content"]
    assert personal.write_soul("owner-alex", "", renamed["version"], delete=True) == {"content": "", "version": None}
    assert personal.get_soul("owner-alex") == {"content": "", "version": None}
    personal.patch_twin("owner-alex", {"name": "After deletion"})
    assert personal.get_soul("owner-alex")["version"] is None and not path.exists()


def test_legacy_database_soul_moves_once_into_file_and_columns_are_removed(product, monkeypatch, tmp_path):
    configured = replace(config.settings, workspace_root=tmp_path / "workspace")
    monkeypatch.setattr(config, "settings", configured)
    monkeypatch.setattr(personal, "settings", configured)
    with service_module.engine.begin() as connection:
        connection.execute(text("ALTER TABLE tt_actors ADD COLUMN soul_content TEXT NOT NULL DEFAULT ''"))
        connection.execute(text("ALTER TABLE tt_actors ADD COLUMN soul_version INTEGER NOT NULL DEFAULT 0"))
        connection.execute(text("UPDATE tt_actors SET soul_content='Keep existing owner persona' WHERE id='twin-alex'"))
    personal_schema.upgrade_personal_schema(service_module.engine)
    current = personal.get_soul("owner-alex")
    assert "Keep existing owner persona" in current["content"] and "SOUL.md" in current["content"]
    assert "soul_content" not in {column["name"] for column in inspect(service_module.engine).get_columns("tt_actors")}
    personal.write_soul("owner-alex", "", current["version"], delete=True)
    personal_schema.upgrade_personal_schema(service_module.engine)
    assert personal.get_soul("owner-alex")["version"] is None
