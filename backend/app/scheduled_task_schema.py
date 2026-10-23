"""Repeatable additive migration for schedules and the existing request journal index."""
from sqlalchemy import text

from .models import ScheduledTask


def upgrade_scheduled_task_schema(engine):
    ScheduledTask.__table__.create(engine, checkfirst=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE INDEX IF NOT EXISTS tt_scheduled_request_history ON tt_reply_requests (command_fingerprint, accepted_at DESC) WHERE source IN ('scheduled_task','system_review')"))


def check_scheduled_task_schema(inspector):
    table = ScheduledTask.__table__
    if (not inspector.has_table(table.name)
            or not set(table.columns.keys()).issubset({c['name'] for c in inspector.get_columns(table.name)})
            or not {i.name for i in table.indexes}.issubset({i['name'] for i in inspector.get_indexes(table.name)})
            or 'tt_scheduled_request_history' not in {i['name'] for i in inspector.get_indexes('tt_reply_requests')}):
        raise RuntimeError("Run python -m app.init_db before starting API or worker")
