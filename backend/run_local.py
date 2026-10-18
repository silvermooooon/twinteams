"""Explicit loopback-only development entrypoint.

Run: uv run --python 3.13 python run_local.py --port 8000
Production deployments must use their verified-identity app entrypoint instead.
"""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    inherited = os.getenv("TWINTEAMS_ENV", "local").strip().lower()
    if inherited != "local":
        parser.error("The local development entrypoint refuses an inherited non-local environment")
    if not os.getenv("DATABASE_URL"):
        parser.error("DATABASE_URL is required")
    os.environ["TWINTEAMS_ENV"] = "local"
    os.environ["TWINTEAMS_DEVELOPMENT_MODE"] = "true"
    os.environ["TWINTEAMS_LOCAL_PROVIDER_CONFIG"] = "true"
    from app.config import local_database

    for variable in ("DATABASE_URL", "AGENT_DB_URL"):
        value = os.getenv(variable)
        if value and not local_database(value):
            parser.error(f"The local entrypoint requires local PostgreSQL for {variable}")
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, timeout_graceful_shutdown=3, ws_max_size=65536, ws_max_queue=64)


if __name__ == "__main__":
    main()
