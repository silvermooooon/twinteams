"""Standalone loopback-only credential page; no Runtime or schema startup.

Run: .venv/bin/python run_credentials.py --port 8012
Use the existing DEV-AAA sign-in cookie from http://127.0.0.1:8010.
"""

from __future__ import annotations

import argparse
import os


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8012)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if os.getenv("TWINTEAMS_ENV", "local").strip().lower() != "local":
        parser.error("The credential page requires the local environment")
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
    from fastapi import FastAPI

    from app.provider_config import router

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        proxy_headers=False,
        access_log=False,
        log_level="warning",
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    main()
