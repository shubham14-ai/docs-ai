"""Periodic repair.

Every mechanism in this service has a failure mode where state is left stranded
rather than wrong. The sweeper is where each of those is repaired, on a timer,
idempotently:

* a worker died holding a document -> its lease expires and the document is
  requeued;
* a retry became due -> promoted from the delay ZSET back onto the stream;
* a stream entry was lost (Redis restart, a crash between ZREM and XADD) -> the
  document is still ``queued`` in MongoDB and is re-enqueued;
* a rate-limit counter drifted -> recomputed from MongoDB, so no user
  permanently loses capacity to a crash.

It runs inside the worker process. Every operation is safe to run concurrently
in several workers, because each ends in a compare-and-set.
"""

import asyncio
from datetime import timedelta

from app.config import Settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.db import REDIS_ERRORS
from app.models.document import utcnow
from app.queue.delay import DelayQueue
from app.queue.stream import DocumentStream
from app.repositories.document import DocumentRepository
from app.services.ratelimit import RateLimiter

log = get_logger(__name__)

# Reconciling counters walks the collection, so it runs less often than the
# lease and retry sweeps.
RECONCILE_EVERY = 20


class Sweeper:
    def __init__(
        self,
        repo: DocumentRepository,
        stream: DocumentStream,
        delay: DelayQueue,
        limiter: RateLimiter,
        settings: Settings,
    ) -> None:
        self._repo = repo
        self._stream = stream
        self._delay = delay
        self._limiter = limiter
        self._settings = settings
        self._stopping = asyncio.Event()
        self._passes = 0

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        log.info("sweeper.started", interval=self._settings.sweeper_interval_seconds)
        while not self._stopping.is_set():
            try:
                await self.sweep_once()
            except AppError as exc:
                # A failed sweep must not kill the loop: the next pass repairs
                # whatever this one missed.
                log.error("sweeper.pass_failed", error_code=exc.code, error=exc.message)
            except REDIS_ERRORS as exc:
                log.error("sweeper.pass_failed", error_code="REDIS_ERROR", error=str(exc))
            await self._wait()
        log.info("sweeper.stopped")

    async def _wait(self) -> None:
        try:
            await asyncio.wait_for(
                self._stopping.wait(), timeout=self._settings.sweeper_interval_seconds
            )
        except asyncio.TimeoutError:
            return  # the normal path: the interval elapsed

    async def sweep_once(self) -> dict[str, int]:
        self._passes += 1
        result = {
            "reclaimed": await self._reclaim_expired_leases(),
            "promoted": await self._delay.promote_due(),
            "requeued": await self._requeue_stale(),
            "reconciled": 0,
        }
        if self._passes % RECONCILE_EVERY == 0:
            result["reconciled"] = await self._limiter.reconcile()

        if any(result.values()):
            log.info("sweeper.pass", **result)
        return result

    async def _reclaim_expired_leases(self) -> int:
        document_ids = await self._repo.reclaim_expired_leases()
        for document_id in document_ids:
            await self._stream.publish(document_id)
        if document_ids:
            log.warning("sweeper.leases_reclaimed", count=len(document_ids))
        return len(document_ids)

    async def _requeue_stale(self) -> int:
        cutoff = utcnow() - timedelta(seconds=self._settings.stale_queued_seconds)
        document_ids = await self._repo.find_stale_queued(cutoff)
        for document_id in document_ids:
            # Re-publishing something still on the stream is harmless: the claim
            # CAS lets exactly one delivery through.
            await self._stream.publish(document_id)
        if document_ids:
            log.warning("sweeper.stale_requeued", count=len(document_ids))
        return len(document_ids)
