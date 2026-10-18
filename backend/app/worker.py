"""Single active execution service: python -m app.worker.

API replicas never start background executors.
Single-active deployment is an operational convention; SDK PG leases fence sessions.
"""
import asyncio
import logging
import signal
from .service import service
from .runtime_bridge import RuntimeBridge


async def main():
    logging.basicConfig(level=logging.INFO)
    await service.db_call('check_initialized')
    runtime = RuntimeBridge(service)
    try:
        await runtime.start()
        service.bind_runtime(runtime)
        await service.start(worker=True)
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stopped.set)
        await stopped.wait()
    finally:
        await service.close()
        await runtime.close()


if __name__ == '__main__':
    asyncio.run(main())
