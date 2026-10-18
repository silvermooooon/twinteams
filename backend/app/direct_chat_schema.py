"""Pair identity uses one nullable unique Chat key, released when the chat becomes a group."""
import hashlib
import json

from sqlalchemy import inspect, text

INDEX = "tt_direct_pair_key"


def pair_key(owner_ids):
    owners = sorted(set(owner_ids))
    if len(owners) != 2:
        raise ValueError("A direct chat key requires exactly two owners")
    return hashlib.sha256(json.dumps(owners, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def upgrade_direct_chat_connection(connection):
    """Provisioning only. Never choose, merge or delete a duplicate conversation."""
    connection.execute(text("ALTER TABLE tt_chats ADD COLUMN IF NOT EXISTS direct_pair_key VARCHAR(64)"))
    pairs = connection.execute(text("""SELECT c.id,c.direct_pair_key,array_agg(m.owner_id ORDER BY m.owner_id) owners
        FROM tt_chats c JOIN tt_memberships m ON m.chat_id=c.id AND m.left_at IS NULL
        WHERE c.kind='group' GROUP BY c.id HAVING count(*)=2 ORDER BY c.id""")).all()
    seen = set()
    for row in pairs:
        key = pair_key(row.owners)
        if key in seen:
            raise RuntimeError("Duplicate active two-owner chats require explicit review before pair-key migration")
        if row.direct_pair_key not in (None, key):
            raise RuntimeError("Existing pair key disagrees with current members; explicit review is required")
        seen.add(key)
    connection.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX} ON tt_chats (direct_pair_key)"))
    check_direct_chat_schema(inspect(connection))
    for row in pairs:
        if row.direct_pair_key is None:
            connection.execute(text("UPDATE tt_chats SET direct_pair_key=:key WHERE id=:id"),
                {"key": pair_key(row.owners), "id": row.id})


def upgrade_direct_chat_schema(engine):
    with engine.begin() as connection:
        upgrade_direct_chat_connection(connection)


def check_direct_chat_schema(inspector):
    columns = {column["name"] for column in inspector.get_columns("tt_chats")}
    indexes = {index["name"]: index for index in inspector.get_indexes("tt_chats")}
    if ("direct_pair_key" not in columns or not indexes.get(INDEX, {}).get("unique")
            or indexes.get(INDEX, {}).get("column_names") != ["direct_pair_key"]):
        raise RuntimeError("Run python -m app.init_db before starting API or worker")
