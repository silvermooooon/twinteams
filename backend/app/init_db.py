"""Explicit repeatable provisioning/upgrade, never an API startup side effect."""
import argparse
import asyncio
from .config import settings
from .service import service


async def main(backfill=False):
    await service.db_call("initialize", create_schema=True)
    from .runtime_bridge import RuntimeBridge
    runtime = RuntimeBridge(service)
    try:
        await runtime.start(initialize=True)
        if backfill:
            from .backfill_activity import backfill_activity
            await service.db_call(backfill_activity)
    finally:
        await runtime.close()
    print(f"Twinteams business and SDK schemas ready for tenant {settings.tenant_id!r}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill-activity", action="store_true", help="Explicit one-time indexed run projection backfill; never replay journals")
    asyncio.run(main(parser.parse_args().backfill_activity))
