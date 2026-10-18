"""Explicit projection-only historical import. Does not read execution journals."""
from sqlalchemy import text
from .config import settings
from .db import engine


def backfill_activity():
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO tt_activity_runs
                (session_id,run_id,chat_id,owner_id,parent_run_id,source,status,title,summary,started_at,finished_at)
            SELECT runs.session_id,runs.run_id,request.chat_id,actor.owner_id,NULL,runs.source,runs.status,
                   left(coalesce(request.input_body,request.raw_owner_input,message.body,'Twin response'),180),
                   NULL,runs.started_at,runs.finished_at
            FROM tt_reply_requests request
            JOIN tt_actors actor ON actor.id=request.target_twin_id
            JOIN tt_chats chat ON chat.id=request.chat_id AND chat.kind='private'
            JOIN agent_runs runs ON runs.tenant_id=:tenant AND runs.session_id=request.session_id AND runs.run_id=request.run_id
            LEFT JOIN tt_messages message ON message.id=request.source_message_id
            ON CONFLICT (session_id,run_id) DO NOTHING
        """), {"tenant": settings.tenant_id})
        connection.execute(text("""
            INSERT INTO tt_activity_runs
                (session_id,run_id,chat_id,owner_id,parent_run_id,source,status,title,summary,started_at,finished_at)
            SELECT child.session_id,child.run_id,parent.chat_id,parent.owner_id,parent.run_id,child.source,child.status,
                   'Background task',NULL,child.started_at,child.finished_at
            FROM tt_activity_runs parent
            JOIN agent_runs child ON child.tenant_id=:tenant AND child.root_run_id=parent.run_id AND child.session_id<>parent.session_id
            WHERE parent.parent_run_id IS NULL AND child.user_id=parent.owner_id
            ON CONFLICT (session_id,run_id) DO NOTHING
        """), {"tenant": settings.tenant_id})
