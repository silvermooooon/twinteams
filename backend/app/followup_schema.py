"""Explicit repeatable additive migration and startup checks for followup storage."""
from .models import TwinFollowup


def upgrade_followup_schema(engine):
    # New table only. Existing message/request/chain records are never rewritten.
    TwinFollowup.__table__.create(engine, checkfirst=True)


def check_followup_schema(inspector):
    table = TwinFollowup.__table__
    if not inspector.has_table(table.name):
        raise RuntimeError("Run python -m app.init_db before starting API or worker")
    columns = {column["name"] for column in inspector.get_columns(table.name)}
    indexes = {index["name"] for index in inspector.get_indexes(table.name)}
    if not set(table.columns.keys()).issubset(columns) or not {"tt_followup_due", "tt_followup_delivery"}.issubset(indexes):
        raise RuntimeError("Run python -m app.init_db before starting API or worker")
