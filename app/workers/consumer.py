"""The worker consume loop.

Two ideas carry this module.

**An async generator separates acquiring work from doing work.** ``claimed()``
yields only documents this worker actually owns; all the queue mechanics --
reading the group, recovering abandoned entries, losing a claim race, acking a
message nobody can process -- stay inside it. The consumer body below is four
lines as a result, and graceful shutdown is just "stop yielding".

**A semaphore acquired before ``create_task`` turns concurrency into
backpressure.** When the worker is saturated the generator stops being pulled,
so it stops reading from the stream, so unclaimed work stays in Redis where
another worker can take it -- instead of accumulating in this process's memory.

``TaskGroup`` rather than fire-and-forget ``create_task``: an exception in a
detached task is discarded with the task object, which is exactly the "worker
silently swallows exceptions" failure mode. In a TaskGroup it propagates.
"""

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator

from app.config import Settings
from app.core.exceptions import AppError
from app.core.logging import bind_context, get_logger
from app.db import REDIS_ERRORS
from app.models.document import DocumentModel
from app.queue.stream import DocumentStream, StreamEntry
from app.repositories.document import DocumentRepository
from app.workers.pipeline import ProcessingPipeline

log = get_logger(__name__)

AUTOCLAIM_EVERY = 10  # read cycles between sweeps of abandoned stream entries
# How long to pause after a Redis error before reading again, so a brief outage
# does not become a busy loop against a struggling server.
RECONNECT_PAUSE_SECONDS = 1.0


@dataclass(frozen=True)
class ClaimedJob:
    entry: StreamEntry
    document: DocumentModel


class DocumentConsumer:
    def __init__(
        self,
        worker_id: str,
        stream: DocumentStream,
        repo: DocumentRepository,
        pipeline: ProcessingPipeline,
        settings: Settings,
    ) -> None:
        self._worker_id = worker_id
        self._stream = stream
        self._repo = repo
        self._pipeline = pipeline
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.worker_concurrency)
        self._stopping = asyncio.Event()
        self._cycles = 0

    def stop(self) -> None:
        """Ask the loop to finish. In-flight documents are allowed to drain."""
        self._stopping.set()

    async def run(self) -> None:
        log.info(
            "worker.started",
            worker_id=self._worker_id,
            concurrency=self._settings.worker_concurrency,
        )
        async with asyncio.TaskGroup() as group:
            async for job in self.claimed():
                await self._semaphore.acquire()
                group.create_task(self._process(job))
        log.info("worker.stopped", worker_id=self._worker_id)

    async def claimed(self) -> AsyncIterator[ClaimedJob]:
        """Yield documents this worker has successfully claimed."""
        while not self._stopping.is_set():
            try:
                entries = await self._next_entries()
            except REDIS_ERRORS as exc:
                # A Redis blip must not end the worker. Nothing is lost:
                # unclaimed entries stay on the stream, and anything claimed but
                # abandoned is recovered by its lease expiring.
                log.warning("worker.queue_unavailable", error=str(exc))
                await asyncio.sleep(RECONNECT_PAUSE_SECONDS)
                continue

            for entry in entries:
                document = await self._repo.claim(
                    entry.document_id, self._worker_id, self._settings.lease_seconds
                )
                if document is None:
                    # Another worker won the CAS, or the document is no longer
                    # queued (already completed, or a redelivered entry after a
                    # lost ack). Either way there is nothing to do: ack and move
                    # on. This is what makes redelivery harmless.
                    log.debug("worker.claim_lost", document_id=entry.document_id)
                    await self._stream.ack(entry.entry_id)
                    continue
                yield ClaimedJob(entry=entry, document=document)

    async def _next_entries(self) -> list[StreamEntry]:
        """New entries, plus periodically those abandoned by a dead worker."""
        self._cycles += 1
        if self._cycles % AUTOCLAIM_EVERY == 0:
            recovered = await self._stream.autoclaim(
                consumer=self._worker_id,
                min_idle_ms=self._settings.lease_seconds * 1_000,
                count=self._settings.worker_concurrency,
            )
            if recovered:
                log.info("worker.autoclaimed", count=len(recovered))
                return recovered

        return await self._stream.read(
            consumer=self._worker_id,
            count=self._settings.worker_concurrency,
            block_ms=int(self._settings.stream_block_seconds * 1_000),
        )

    async def _process(self, job: ClaimedJob) -> None:
        """Run one document, then always ack and always free the slot.

        The ack happens even on failure: the pipeline has already recorded the
        outcome in MongoDB and, if a retry is due, re-enqueued the document
        through the delay queue. Leaving the entry pending would double it up.

        An error escaping the pipeline is always typed (``@guarded`` sees to
        that) and is logged with its traceback, then contained: the document is
        left in ``processing`` with a lease that will expire, and the sweeper
        requeues it. Letting it propagate would tear down the TaskGroup and take
        every other in-flight document with it -- a worse outcome for the same
        underlying bug. Cancellation is deliberately not caught, so shutdown
        still works.
        """
        try:
            with bind_context(worker_id=self._worker_id):
                await self._pipeline.run(job.document)
        except AppError as exc:
            log.error(
                "worker.job_error",
                document_id=str(job.document.id),
                error_code=exc.code,
                error=exc.message,
                details=exc.details,
            )
        finally:
            await self._stream.ack(job.entry.entry_id)
            self._semaphore.release()
