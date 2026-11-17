"""Tests never open the application's local database or configured provider."""

import os
import tempfile
from pathlib import Path

from checks.postgres import TestDatabase

_directory = tempfile.TemporaryDirectory(prefix="twinteams-tests-")
_database = TestDatabase().configure()
os.environ.update(
    TWINTEAMS_ENV="local",
    TWINTEAMS_DEVELOPMENT_MODE="true",
    AGENT_API="offline-regression",
    OPENAI_API_KEY="offline-no-network",
    AGENT_MEMORY_DIR=str(Path(_directory.name) / "memory"),
    TWINTEAMS_WORKSPACE_ROOT=str(Path(_directory.name) / "workspace"),
)


import pytest
from agent_runtime import AssistantMessageEventStream, Models, assistant_message


@pytest.fixture(autouse=True)
def isolate_activity_title_calls(request, monkeypatch):
    """Keep Activity title generation from reaching conversation scripted providers."""
    if request.module.__name__.rsplit(".", 1)[-1] in {"test_activity_titles", "test_activity_visibility"}:
        return
    original = Models.register_api

    def register_api(self, api, stream_fn):
        def wrapped(model, context, options=None):
            if (options or {}).get("_purpose") == "activity_title":
                stream = AssistantMessageEventStream()
                stream.push({"type": "done", "message": assistant_message(
                    model, content=[{"type": "text", "text": "Review the request"}],
                    stopReason="stop")})
                return stream
            try:
                return stream_fn(model, context, options)
            except TypeError:
                return stream_fn(model, context)
        return original(self, api, wrapped)

    monkeypatch.setattr(Models, "register_api", register_api)
