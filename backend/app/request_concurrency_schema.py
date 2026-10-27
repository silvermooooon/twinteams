"""Add task ancestry without rewriting existing SDK execution identities."""
from sqlalchemy import text


def upgrade_request_concurrency_schema(engine):
    with engine.begin() as connection:
        connection.execute(text("""ALTER TABLE tt_reply_requests ADD COLUMN IF NOT EXISTS
            continuation_of_request_id VARCHAR(80) REFERENCES tt_reply_requests(id)"""))
        connection.execute(text("""ALTER TABLE tt_reply_requests ADD COLUMN IF NOT EXISTS
            background BOOLEAN NOT NULL DEFAULT FALSE"""))
        connection.execute(text("""CREATE INDEX IF NOT EXISTS ix_tt_reply_requests_continuation_of_request_id
            ON tt_reply_requests (continuation_of_request_id)"""))
        connection.execute(text("""CREATE INDEX IF NOT EXISTS tt_request_session_order
            ON tt_reply_requests (session_id, accepted_at, accepted_seq)
            WHERE status IN ('queued','running')"""))
        connection.execute(text("""CREATE INDEX IF NOT EXISTS tt_activity_live_children
            ON tt_activity_runs (chat_id, owner_id, private_generation, started_at)
            WHERE source='subagent' AND status='running'"""))
        connection.execute(text("""UPDATE tt_reply_requests SET background=TRUE
            WHERE (source IN ('auto','scheduled_task','system_review','memory_dream')
                OR execution_config ->> 'scheduled_task' IS NOT NULL) AND background=FALSE"""))


def check_request_concurrency_schema(inspector):
    columns = {column["name"] for column in inspector.get_columns("tt_reply_requests")}
    if not {"continuation_of_request_id", "background"}.issubset(columns):
        raise RuntimeError("Run the request concurrency schema upgrade before starting API or worker")
