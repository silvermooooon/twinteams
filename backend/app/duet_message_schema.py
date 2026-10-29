"""Explicit breaking upgrade to message-driven Duet channels; never run at API startup."""
from sqlalchemy import text

RETIRED_TABLES = ("tt_automatic_reservations", "tt_trigger_candidates", "tt_routing_decisions", "tt_twin_followups")


def upgrade_duet_message_schema(engine):
    from importlib.resources import files
    ddl = files("app").joinpath("sql", "006_duet_message_channels.sql").read_text()
    with engine.begin() as connection:
        connection.execute(text(ddl))


def check_duet_message_schema(inspector):
    columns = {column["name"]: column for column in inspector.get_columns("tt_messages")}
    requests = {column["name"] for column in inspector.get_columns("tt_reply_requests")}
    checks = inspector.get_check_constraints("tt_messages")
    if (not columns["seq"]["nullable"] or "automatic_reservation_id" in requests
            or any(inspector.has_table(table) for table in RETIRED_TABLES)
            or not any(row["name"] == "tt_message_state" and "pending" in row["sqltext"] for row in checks)
            or "tt_pending_duet_sends" not in {row["name"] for row in inspector.get_indexes("tt_messages")}):
        raise RuntimeError("Run the explicit Duet message schema upgrade before starting API or worker")
