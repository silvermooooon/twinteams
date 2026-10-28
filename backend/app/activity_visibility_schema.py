"""Owner Activity presentation only; execution and internal activity stay intact."""
from sqlalchemy import inspect, text

HIDDEN_ACTIVITY_SOURCES = ("scheduled_task", "system_review", "memory_dream")


def backfill_activity_visibility(connection):
    """Explicit historical classification, never run by activity reads or schema checks."""
    connection.execute(text("""
        WITH RECURSIVE hidden_requests AS (
            SELECT id, chat_id, target_twin_id, private_generation
            FROM tt_reply_requests
            WHERE source = ANY(:sources)
                OR execution_config ->> 'scheduled_task' IS NOT NULL
            UNION
            SELECT child.id, child.chat_id, child.target_twin_id, child.private_generation
            FROM tt_reply_requests child
            JOIN hidden_requests parent ON child.continuation_of_request_id = parent.id
                AND child.chat_id = parent.chat_id AND child.target_twin_id = parent.target_twin_id
                AND coalesce(child.private_generation, 0) = coalesce(parent.private_generation, 0)
        ), hidden_runs AS (
            SELECT activity.session_id, activity.run_id, activity.chat_id,
                activity.owner_id, activity.private_generation
            FROM tt_activity_runs activity
            WHERE activity.source = ANY(:sources) OR EXISTS (
                SELECT 1 FROM hidden_requests hidden
                JOIN tt_reply_requests request ON request.id = hidden.id
                JOIN tt_actors actor ON actor.id = request.target_twin_id
                WHERE request.session_id = activity.session_id AND request.run_id = activity.run_id
                    AND request.chat_id = activity.chat_id AND actor.owner_id = activity.owner_id
                    AND coalesce(request.private_generation, 0) = activity.private_generation
            )
            UNION
            SELECT child.session_id, child.run_id, child.chat_id, child.owner_id, child.private_generation
            FROM tt_activity_runs child
            JOIN hidden_runs parent ON (child.parent_run_id = parent.run_id
                OR child.parent_run_id = parent.session_id || '/' || parent.run_id)
                AND child.chat_id = parent.chat_id AND child.owner_id = parent.owner_id
                AND child.private_generation = parent.private_generation
        )
        UPDATE tt_activity_runs activity SET user_visible = FALSE
        FROM hidden_runs hidden
        WHERE activity.session_id = hidden.session_id AND activity.run_id = hidden.run_id
            AND activity.user_visible
    """), {"sources": list(HIDDEN_ACTIVITY_SOURCES)})


def upgrade_activity_visibility_schema(engine):
    with engine.begin() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("tt_activity_runs")}
        if "user_visible" not in columns:
            connection.execute(text("ALTER TABLE tt_activity_runs ADD COLUMN user_visible BOOLEAN NOT NULL DEFAULT TRUE"))
            backfill_activity_visibility(connection)
        connection.execute(text("""CREATE INDEX IF NOT EXISTS tt_activity_user_visible_order
            ON tt_activity_runs (chat_id, private_generation, started_at, session_id, run_id)
            WHERE user_visible"""))


def check_activity_visibility_schema(inspector):
    columns = {column["name"] for column in inspector.get_columns("tt_activity_runs")}
    indexes = {index["name"] for index in inspector.get_indexes("tt_activity_runs")}
    if "user_visible" not in columns or "tt_activity_user_visible_order" not in indexes:
        raise RuntimeError("Run the activity visibility schema upgrade before starting API or worker")
