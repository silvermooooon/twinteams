"""Explicit additive reaction provisioning, without seeding or runtime calls."""
from sqlalchemy import inspect

from .models import MessageReaction


def upgrade_reaction_schema(engine):
    MessageReaction.__table__.create(engine, checkfirst=True)
    check_reaction_schema(inspect(engine))


def check_reaction_schema(inspector):
    table = MessageReaction.__table__
    if not inspector.has_table(table.name):
        raise RuntimeError("Run python -m app.migrate_reactions before starting API or worker")
    columns = {row["name"] for row in inspector.get_columns(table.name)}
    pk = inspector.get_pk_constraint(table.name).get("constrained_columns")
    foreign_keys = {tuple(row["constrained_columns"]): row for row in inspector.get_foreign_keys(table.name)}
    message_fk = foreign_keys.get(("message_id",), {})
    actor_fk = foreign_keys.get(("actor_id",), {})
    if (not set(table.columns.keys()).issubset(columns) or pk != ["message_id", "actor_id", "emoji"]
            or message_fk.get("referred_table") != "tt_messages" or message_fk.get("referred_columns") != ["id"]
            or message_fk.get("options", {}).get("ondelete") != "CASCADE"
            or actor_fk.get("referred_table") != "tt_actors" or actor_fk.get("referred_columns") != ["id"]):
        raise RuntimeError("Reaction schema differs; run python -m app.migrate_reactions before startup")
