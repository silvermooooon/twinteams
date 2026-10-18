"""Tests never open the application's local database or configured provider."""
import os
import tempfile
from pathlib import Path

_directory = tempfile.TemporaryDirectory(prefix="twinteams-tests-")
os.environ.update(
    DATABASE_URL="sqlite:///" + str(Path(_directory.name) / "test.db"),
    AGENT_DB_URL="sqlite:///" + str(Path(_directory.name) / "runtime.db"),
    TWINTEAMS_ENV="local", TWINTEAMS_DEVELOPMENT_MODE="true",
    AGENT_API="offline-regression", OPENAI_API_KEY="offline-no-network",
    AGENT_MEMORY_DIR=str(Path(_directory.name) / "memory"),
    TWINTEAMS_WORKSPACE_ROOT=str(Path(_directory.name) / "workspace"),
)
