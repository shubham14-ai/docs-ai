"""Redis Streams as the work queue.

Why a stream and not a Mongo polling loop: Redis is already a required
dependency, so this adds no infrastructure, and a consumer group gives
push-based delivery (no polling latency), per-consumer ownership of a message,
and ``XAUTOCLAIM`` to recover work from a worker that died mid-job.

MongoDB stays the source of truth. If the stream loses an entry the sweeper
re-enqueues it from Mongo, so Redis is an accelerator and never the record.
"""

from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.config import Settings
from app.core.exceptions import DependencyUnavailable
from app.core.logging import get_logger
from app.db import REDIS_ERRORS

log = get_logger(__name__)

FIELD_DOCUMENT_ID = "document_id"


@dataclass(frozen=True)
class StreamEntry:
    entry_id: str
    document_id: str


class DocumentStream:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings
        self._key = settings.stream_key
        self._group = settings.consumer_group

    async def ensure_group(self) -> None:
        """Create the consumer group, tolerating "already exists".

        ``mkstream=True`` so a fresh deployment does not need the stream to
        exist first.
        """
        try:
            await self._redis.xgroup_create(
                self._key, self._group, id="0", mkstream=True
            )
            log.info("stream.group_created", stream=self._key, group=self._group)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
            log.debug("stream.group_exists", stream=self._key, group=self._group)

    async def publish(self, document_id: str) -> str:
        """Enqueue. Failure here is a 503, never a silent drop.

        Accepting a submission we cannot enqueue would lose the work: the client
        gets a 201 and the document sits queued forever.
        """
        try:
            return await self._redis.xadd(self._key, {FIELD_DOCUMENT_ID: document_id})
        except REDIS_ERRORS as exc:
            raise DependencyUnavailable(
                "Cannot enqueue document for processing: queue is unavailable.",
                {"dependency": "redis"},
            ) from exc

    async def read(
        self, consumer: str, count: int, block_ms: int
    ) -> list[StreamEntry]:
        response = await self._redis.xreadgroup(
            groupname=self._group,
            consumername=consumer,
            streams={self._key: ">"},
            count=count,
            block=block_ms,
        )
        entries: list[StreamEntry] = []
        for _stream, messages in response or []:
            for entry_id, fields in messages:
                document_id = (fields or {}).get(FIELD_DOCUMENT_ID)
                if document_id:
                    entries.append(StreamEntry(entry_id, document_id))
                else:
                    # Nothing can ever process this. Acknowledge it rather than
                    # leaving it pending forever.
                    log.warning("stream.malformed_entry", entry_id=entry_id)
                    await self.ack(entry_id)
        return entries

    async def autoclaim(self, consumer: str, min_idle_ms: int, count: int) -> list[StreamEntry]:
        """Take over entries another consumer read but never acknowledged.

        This is the stream-side half of crash recovery; the Mongo lease is the
        other half. Both are needed: the stream entry and the document state can
        be stranded independently.
        """
        try:
            _, messages, _ = await self._redis.xautoclaim(
                name=self._key,
                groupname=self._group,
                consumername=consumer,
                min_idle_time=min_idle_ms,
                count=count,
            )
        except ResponseError as exc:
            if "NOGROUP" in str(exc):
                await self.ensure_group()
                return []
            raise
        return [
            StreamEntry(entry_id=entry_id, document_id=fields[FIELD_DOCUMENT_ID])
            for entry_id, fields in messages
            if fields and FIELD_DOCUMENT_ID in fields
        ]

    async def ack(self, entry_id: str) -> None:
        await self._redis.xack(self._key, self._group, entry_id)

    async def depth(self) -> int:
        return await self._redis.xlen(self._key)
