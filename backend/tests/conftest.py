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
