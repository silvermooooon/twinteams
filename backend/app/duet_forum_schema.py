"""Explicit additive forum upgrade; startup only checks its installed shape."""
from importlib.resources import files

from sqlalchemy import text


def upgrade_duet_forum_schema(engine):
    with engine.begin() as connection:
        connection.execute(text(files("app").joinpath("sql", "007_duet_forum_topics.sql").read_text()))


def check_duet_forum_schema(inspector):
    message_columns = {column["name"] for column in inspector.get_columns("tt_messages")}
    topic_columns = {column["name"] for column in inspector.get_columns("tt_duet_conversations")}
    attachment_columns = {column["name"] for column in inspector.get_columns("tt_chat_attachments")}
    if (not {"mention_owner_ids", "revision", "edited_at"}.issubset(message_columns)
            or not {"opening_mention_owner_ids", "revision"}.issubset(topic_columns)
            or "granted_owner_ids" not in attachment_columns
            or not inspector.has_table("tt_duet_post_revisions")):
        raise RuntimeError("Run the explicit Duet forum schema upgrade before starting API or worker")
