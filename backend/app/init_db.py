"""Explicit schema provisioning: uv run --python 3.13 python -m app.init_db.

Use a separate database per TWINTEAMS_TENANT_ID. This initializes schemas; it is
not a destructive reset or an upgrade migration for existing production tables.
"""
import asyncio
from .config import settings
from .service import service


async def main():
    service.initialize(create_schema=True)
    from .runtime_bridge import RuntimeBridge
    runtime = RuntimeBridge(service)
    try:
        await runtime.start(initialize=True)
    finally:
        await runtime.close()
    print(f"Twinteams business and SDK schemas initialized for tenant {settings.tenant_id!r}.")


if __name__ == "__main__":
    asyncio.run(main())
