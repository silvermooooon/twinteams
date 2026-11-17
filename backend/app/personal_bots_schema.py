"""Personal BOT provisioning is explicit; startup checks the installed schema."""
from importlib.resources import files

from sqlalchemy import text


def upgrade_personal_bots_schema(engine):
    with engine.begin() as connection:
        connection.execute(text(files("app").joinpath("sql", "008_personal_bots.sql").read_text()))


def check_personal_bots_schema(inspector):
    columns = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in ("tt_actors", "tt_chats", "tt_messages", "tt_reply_requests")
    }
    actor_indexes = {index["name"]: index for index in inspector.get_indexes("tt_actors")}
    chat_indexes = {index["name"] for index in inspector.get_indexes("tt_chats")}
    if ("assignment" not in columns["tt_actors"]
            or not {"internal_owner_id", "bot_id", "member_actor_ids"}.issubset(columns["tt_chats"])
            or "coordination" not in columns["tt_messages"]
            or "bot_handoff" not in columns["tt_reply_requests"]
            or not actor_indexes.get("tt_owner_primary_actors", {}).get("unique")
            or "tt_bot_handoff_pending" not in {index["name"] for index in inspector.get_indexes("tt_reply_requests")}
            or "tt_internal_owner_chats" not in chat_indexes):
        raise RuntimeError("Run python -m app.init_db to provision personal BOTs before starting API or worker")
