"""Explicit additive snapshot provisioning and startup validation."""
from importlib.resources import files

from sqlalchemy import text


def upgrade_bot_sharing_schema(engine):
    with engine.begin() as connection:
        connection.execute(text(files("app").joinpath("sql", "009_bot_sharing.sql").read_text()))


def check_bot_sharing_schema(inspector):
    tables = {"tt_bot_snapshots", "tt_bot_share_tokens", "tt_bot_installs"}
    columns = {column["name"] for column in inspector.get_columns("tt_actors")}
    if not tables.issubset(inspector.get_table_names()) or not {
            "bot_skill_pack_digest", "installed_snapshot_id"}.issubset(columns):
        raise RuntimeError("Run python -m app.init_db to provision BOT sharing before starting API or worker")
    with inspector.bind.connect() as connection:
        immutable = connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_trigger "
            "WHERE tgname = 'tt_bot_snapshot_immutable' AND tgrelid = 'tt_bot_snapshots'::regclass)"))
    if not immutable:
        raise RuntimeError("Run python -m app.init_db to provision immutable BOT snapshots")
