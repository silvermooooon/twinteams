"""Disposable Redis hints. PostgreSQL remains the only durable authority."""
import asyncio
import json
import logging
from contextlib import suppress

from redis.asyncio import Redis
from .config import settings

log = logging.getLogger(__name__)


class Notifications:
    def __init__(self):
        self.redis = None
        self.loop = None
        self.task = None
        self.listeners = set()
        self.channel = f"{settings.redis_prefix}:changes"
        self.healthy = False
        self.worker_status = {}

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=None)
        try:
            value = await self.redis.get(settings.redis_prefix + ":worker")
            self.worker_status = json.loads(value) if value else {}
        except Exception:
            pass
        self.task = asyncio.create_task(self._listen())

    async def _listen(self):
        while True:
            try:
                async with self.redis.pubsub() as pubsub:
                    await pubsub.subscribe(self.channel)
                    self.healthy = True
                    async for message in pubsub.listen():
                        if message['type'] == 'message':
                            event = json.loads(message['data'])
                            if event.get("worker_status"):
                                self.worker_status = event["worker_status"]
                            for listener in tuple(self.listeners):
                                listener(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.healthy = False
                log.warning('Redis notifications unavailable; PostgreSQL compensation remains active')
                await asyncio.sleep(2)

    def _emit(self, event):
        def schedule():
            for listener in tuple(self.listeners):
                listener(event)
            asyncio.create_task(self.publish(event))
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(schedule)

    def emit(self, chat_id=None, *, work=False):
        self._emit({'chat_id': chat_id, 'kind': 'work' if work else 'changed'})

    def emit_actor(self, actor_id, profile_revision):
        self._emit({'type': 'actor_changed', 'actor_id': actor_id,
                    'profile_revision': profile_revision})

    def emit_personal(self, owner_id, section):
        self._emit({'type': 'personal_changed', 'owner_id': owner_id, 'section': section})

    async def publish(self, event):
        try:
            if self.redis:
                await self.redis.publish(self.channel, json.dumps(event))
        except Exception:
            self.healthy = False

    async def report_worker(self, configured, **details):
        import time
        observed_at = time.time()
        status = {"configured": bool(configured), "observed_at": observed_at,
                  "expires_at": observed_at + max(120, settings.compensation_seconds * 3),
                  **{name: details.get(name) for name in ("provider", "model", "api", "memory")}}
        try:
            await self.redis.set(settings.redis_prefix + ":worker", json.dumps(status), ex=max(120, settings.compensation_seconds * 3))
            await self.publish({"worker_status": status})
        except Exception:
            pass

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        if self.redis:
            await self.redis.aclose()
        self.task = None
        self.loop = None
        self.redis = None


notifications = Notifications()
