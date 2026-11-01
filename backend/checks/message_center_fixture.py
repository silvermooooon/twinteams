"""Isolated process fixture with a deterministic SDK provider; no model network calls."""
import argparse
import json
import os
import runpy
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=["init", "api", "worker"])
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    if os.getenv("MESSAGE_CENTER_ACCEPTANCE") != "true" or "tt_test_" not in os.environ["DATABASE_URL"]:
        raise RuntimeError("Disposable acceptance database required")
    from agent_runtime import AssistantMessageEventStream, assistant_message

    from app.runtime_bridge import RuntimeBridge, text_content

    original = RuntimeBridge.__init__
    def offline(model, context, options):
        position = max(index for index, row in enumerate(context.messages) if row["role"] == "user")
        user = text_content(context.messages[position])
        results = [row for row in context.messages[position + 1:] if row["role"] == "toolResult"]
        content = []
        if not results and user.startswith("CENTER PUBLISH "):
            title = user.removeprefix("CENTER PUBLISH ")
            content = [{"type": "toolCall", "id": uuid4().hex, "name": "publish_center_message",
                "arguments": {"title": title, "body": "Team briefing", "recipients": ["owner-maya"]}}]
        elif not results and user.startswith("CENTER ATTEND "):
            content = [{"type": "toolCall", "id": uuid4().hex, "name": "respond_center_message",
                "arguments": {"id": user.removeprefix("CENTER ATTEND "), "response": "attending", "note": "Confirmed by Maya"}}]
        elif user.startswith("A Message Center delivery") and '"title": "report"' in user:
            content = [{"type": "text", "text": "A useful briefing arrived."}]
        elif user.startswith("This is the platform's periodic private system review") and '"title": "digest"' in user:
            content = [{"type": "text", "text": "A digest briefing arrived."}]
        elif results:
            if any(row.get("isError") for row in results):
                raise RuntimeError("Fixture tool failed: " + json.dumps(results, default=str))
            content = [{"type": "text", "text": "Action completed."}]
        stream = AssistantMessageEventStream()
        stream.push({"type": "done", "message": assistant_message(model, content=content,
            stopReason="toolUse" if any(row["type"] == "toolCall" for row in content) else "stop")})
        return stream

    def initialize(self, *positional, **named):
        original(self, *positional, **named)
        self.models.register_api("center-offline", offline)
    RuntimeBridge.__init__ = initialize
    if args.role == "init":
        from app.service import service
        service.initialize(create_schema=True)
    elif args.role == "api":
        import uvicorn
        uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, log_level="warning")
    else:
        runpy.run_module("app.worker", run_name="__main__")


if __name__ == "__main__":
    main()
