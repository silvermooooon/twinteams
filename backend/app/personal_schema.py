"""Additive personal-profile migration; called only by explicit provisioning."""
from sqlalchemy import inspect, text

PERSONAL_COLUMNS = {
    "tt_actors": {
        "avatar_data": "BYTEA",
        "avatar_digest": "VARCHAR(64)",
        "avatar_preset": "VARCHAR(24)",
        "profile_revision": "BIGINT NOT NULL DEFAULT 1",
        "enabled_skill_names": "JSON NOT NULL DEFAULT '[]'",
    },
    "tt_reply_requests": {"execution_config": "JSON", "private_generation": "INTEGER"},
    "tt_chats": {"reset_generation": "INTEGER NOT NULL DEFAULT 0", "reset_pending": "BOOLEAN NOT NULL DEFAULT FALSE"},
    "tt_activity_runs": {"private_generation": "INTEGER NOT NULL DEFAULT 0"},
    "tt_memory_edit_audit": {"private_generation": "INTEGER NOT NULL DEFAULT 0"},
    "tt_credential_requests": {
        "runtime_session_id": "VARCHAR(128)",
        "runtime_run_id": "VARCHAR(128)",
        "runtime_command_id": "VARCHAR(256)",
    },
}


def upgrade_personal_schema(engine):
    from .models import MemoryDreamState, MemoryEditAudit, TwinReset
    MemoryEditAudit.__table__.create(engine, checkfirst=True)
    MemoryDreamState.__table__.create(engine, checkfirst=True)
    TwinReset.__table__.create(engine, checkfirst=True)
    with engine.begin() as connection:
        for table, columns in PERSONAL_COLUMNS.items():
            for name, declaration in columns.items():
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {declaration}"))
        from .dream_scope_schema import upgrade
        upgrade(connection)
        chat_columns = {column["name"] for column in inspect(connection).get_columns("tt_chats")}
        if "kind" in chat_columns:
            connection.execute(text("UPDATE tt_reply_requests r SET private_generation=0 FROM tt_chats c WHERE r.chat_id=c.id AND c.kind='private' AND r.private_generation IS NULL"))
        actor_columns = {column["name"] for column in inspect(connection).get_columns("tt_actors")}
        if "soul_content" in actor_columns:
            from .config import settings
            from .soul_file import initialize
            for row in connection.execute(text("SELECT a.name,a.soul_content,c.id FROM tt_actors a JOIN tt_chats c ON c.private_owner_id=a.owner_id WHERE a.kind='twin' AND c.kind='private' FOR UPDATE OF a")):
                workspace = settings.workspace_root / row.id
                if (row.soul_content and (workspace / "SOUL.md").exists()
                        and not (workspace / ".soul-initialized").exists()
                        and row.soul_content not in (workspace / "SOUL.md").read_text(encoding="utf-8")):
                    raise RuntimeError("Existing SOUL.md differs from legacy Soul; reconcile both before migration")
                initialize(workspace, row.name, row.soul_content)
            connection.execute(text("ALTER TABLE tt_actors DROP COLUMN soul_content"))
            connection.execute(text("ALTER TABLE tt_actors DROP COLUMN IF EXISTS soul_version"))


def check_personal_schema(inspector):
    from .models import MemoryDreamState, MemoryEditAudit, TwinReset
    for model in (MemoryEditAudit, MemoryDreamState, TwinReset):
        table = model.__table__
        if not inspector.has_table(table.name) or not set(table.columns.keys()).issubset(
                {column["name"] for column in inspector.get_columns(table.name)}):
            raise RuntimeError("Run python -m app.init_db before starting API or worker")
        indexes = {index["name"] for index in inspector.get_indexes(table.name)}
        if not {index.name for index in table.indexes}.issubset(indexes):
            raise RuntimeError("Run python -m app.init_db before starting API or worker")
    if not inspector.has_table("tt_credential_requests"):
        raise RuntimeError("Run python -m app.init_db before starting API or worker")
    for table, columns in PERSONAL_COLUMNS.items():
        if not inspector.has_table(table) or not set(columns).issubset(
            {column["name"] for column in inspector.get_columns(table)}
        ):
            raise RuntimeError("Run python -m app.init_db before starting API or worker")
