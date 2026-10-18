"""No-auth connectivity probe; never sends or reads a credential.

Run this in the same desktop/server network namespace as the local backend:
    .venv/bin/python checks/provider_connectivity_probe.py

Only status, elapsed time, and exception class names are printed. An HTTP 401
is a useful connectivity result; this probe does not validate any API key.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx


async def main():
    logging.disable(logging.CRITICAL)
    started = time.monotonic()
    result = {"http_status": None, "exception_class": None, "cause_class": None}
    try:
        async with asyncio.timeout(15):
            async with httpx.AsyncClient(
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(10, connect=10),
            ) as client:
                # Read response headers only. No Authorization header, body,
                # environment output, custom endpoint, redirect, or retry.
                async with client.stream("GET", "https://api.openai.com/v1/models") as response:
                    result["http_status"] = response.status_code
    except Exception as exc:  # Never stringify an exception or print a traceback.
        result["exception_class"] = type(exc).__name__
        if exc.__cause__ is not None:
            result["cause_class"] = type(exc.__cause__).__name__
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
