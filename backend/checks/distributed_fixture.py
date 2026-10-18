"""Explicit isolated multi-process fixture. Never a normal application entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=["api", "worker", "init"])
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    if (
        not os.getenv("TWINTEAMS_DISTRIBUTED_TEST")
        or not os.environ["DATABASE_URL"].find("tt_test_") >= 0
    ):
        raise RuntimeError("The explicit test marker and disposable schema are required")
    import httpx

    async def deny_outbound_http(*args, **kwargs):
        raise AssertionError("The distributed fixture forbids outbound model HTTP")

    def deny_outbound_sync_http(*args, **kwargs):
        raise AssertionError("The distributed fixture forbids outbound model HTTP")

    httpx.AsyncClient.send = deny_outbound_http
    httpx.Client.send = deny_outbound_sync_http
    if os.getenv("DISTRIBUTED_SQL_FILE"):
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        @event.listens_for(Engine, "before_cursor_execute")
        def count_sql(connection, cursor, statement, parameters, context, executemany):
            # Only statement text, never bound values or connection configuration.
            with Path(os.environ["DISTRIBUTED_SQL_FILE"]).open("a") as target:
                target.write(
                    json.dumps(
                        {"role": args.role, "pid": os.getpid(), "sql": " ".join(statement.split())}
                    )
                    + "\n"
                )

    from agent_runtime import AssistantMessageEventStream, assistant_message

    from app.runtime_bridge import RuntimeBridge

    original = RuntimeBridge.__init__

    def record_call():
        with Path(os.environ["DISTRIBUTED_CALLS_FILE"]).open("a") as target:
            target.write(json.dumps({"role": args.role, "pid": os.getpid()}) + "\n")

    async def offline(model, context, options):
        await asyncio.to_thread(record_call)
        latest_input = next(
            (message for message in reversed(context.messages) if message.get("role") == "user"), {}
        )
        if "SLOW_CANCEL" in json.dumps(latest_input):
            await options["signal"].run(asyncio.sleep(20))
        await asyncio.sleep(0.05)
        stream = AssistantMessageEventStream()
        stream.push(
            {
                "type": "done",
                "message": assistant_message(
                    model,
                    content=[
                        {"type": "text", "text": "Explicit offline distributed acceptance result."}
                    ],
                    stopReason="stop",
                ),
            }
        )
        return stream

    def initialize(self, *positional, **named):
        original(self, *positional, **named)
        self.models.register_api("distributed-offline", offline)

    RuntimeBridge.__init__ = initialize
    from app.notifications import notifications

    original_emit = notifications.emit

    def emit_unless_injected_loss(*args, **kwargs):
        control = os.getenv("DISTRIBUTED_HINT_DROP_FILE")
        if control and Path(control).exists():
            return
        original_emit(*args, **kwargs)

    notifications.emit = emit_unless_injected_loss
    if args.role == "init":
        # TestDatabase already installed the canonical SDK migrations.
        from app.service import service

        service.initialize(create_schema=True)
    elif args.role == "api":
        import uvicorn

        uvicorn.run(
            "app.main:app",
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
            ws_max_size=65536,
            ws_max_queue=64,
        )
    else:
        runpy.run_module("app.worker", run_name="__main__")


if __name__ == "__main__":
    main()
