"""Delayed retry queue: a Redis sorted set scored by "ready at" timestamp.

A failed document must not be retried in place. The job takes 10-30 seconds, so
sleeping inside the worker would hold a concurrency slot doing nothing, and if
that process dies during the sleep the retry is lost. Putting the document back
with a due time frees the slot immediately and lets any worker pick it up.

Promotion (due -> stream) is one Lua script so two workers cannot promote the
same document twice.
"""

import time

from redis.asyncio import Redis

from app.config import Settings
from app.core.logging import get_logger
from app.db import REDIS_ERRORS
from app.queue.stream import DocumentStream

log = get_logger(__name__)

# Read and remove due members in one atomic step. Without this, two sweepers
# could both read the same member before either removed it.
_POP_DUE_LUA = """
local due = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, tonumber(ARGV[2]))
if #due > 0 then
  redis.call('ZREM', KEYS[1], unpack(due))
end
return due
"""


class DelayQueue:
    def __init__(self, redis: Redis, stream: DocumentStream, settings: Settings) -> None:
        self._redis = redis
        self._stream = stream
        self._settings = settings
        self._key = settings.retry_zset_key
        self._pop_due = redis.register_script(_POP_DUE_LUA)

    async def schedule(self, document_id: str, delay_seconds: float) -> None:
        ready_at = time.time() + delay_seconds
        try:
            await self._redis.zadd(self._key, {document_id: ready_at})
            log.info(
                "retry.scheduled",
                document_id=document_id,
                delay_seconds=round(delay_seconds, 2),
            )
        except REDIS_ERRORS as exc:
            # The document is already back in `queued` in MongoDB, so the
            # sweeper's stale-queued scan will re-enqueue it. Slower than the
            # intended backoff, but nothing is lost.
            log.warning(
                "retry.schedule_failed", document_id=document_id, error=str(exc)
            )

    async def promote_due(self, limit: int = 100) -> int:
        """Move every due document back onto the stream. Returns how many."""
        try:
            due = await self._pop_due(keys=[self._key], args=[time.time(), limit])
        except REDIS_ERRORS as exc:
            log.warning("retry.promote_failed", error=str(exc))
            return 0

        for document_id in due:
            # A crash between ZREM and XADD leaves the document `queued` in
            # MongoDB with no stream entry; the stale-queued sweep recovers it.
            await self._stream.publish(document_id)

        if due:
            log.info("retry.promoted", count=len(due))
        return len(due)

    async def pending(self) -> int:
        return await self._redis.zcard(self._key)
