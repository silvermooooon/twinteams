"""Explicit loopback-only development entrypoint.

Run: uv run --python 3.13 python run_local.py --port 8000
Production deployments must use their verified-identity app entrypoint instead.
"""
from __future__ import annotations

import argparse
import os
from urllib.parse import urlsplit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    inherited = os.getenv("TWINTEAMS_ENV", "local").strip().lower()
    if inherited != "local":
        parser.error("The local development entrypoint refuses an inherited non-local environment")
    for variable in ("DATABASE_URL", "AGENT_DB_URL"):
        value = os.getenv(variable)
        if value and urlsplit(value.strip()).scheme not in {"sqlite", "sqlite+pysqlite"}:
            parser.error(f"The local development entrypoint requires SQLite for {variable}")
    # Set explicit opt-in before importing uvicorn or any application module.
    os.environ["TWINTEAMS_ENV"] = "local"
    os.environ["TWINTEAMS_DEVELOPMENT_MODE"] = "true"
    os.environ["TWINTEAMS_LOCAL_PROVIDER_CONFIG"] = "true"
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()
