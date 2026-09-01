"""What happens to one claimed document: process, then settle it.

Settling is where correctness lives. Every terminal path must do three things
together -- write the outcome to MongoDB, release the rate-limit slot, and clear
the in-flight guard -- and must do them at most once even if the same document
is delivered twice. The MongoDB compare-and-set is what provides the "at most
once": the Redis side effects run only when the CAS matched.
"""

from datetime import timedelta

from app.config import Settings
from app.core.decorators import backoff_delay, guarded, log_stage, timed
from app.core.exceptions import AppError
from app.core.logging import bind_context, get_logger
from app.models.document import DocumentModel, ProcessingError, utcnow
from app.queue.delay import DelayQueue
from app.repositories.document import DocumentRepository
from app.services.cache import SummaryCache
from app.services.ratelimit import RateLimiter
from app.services.summarizer import Summarizer

log = get_logger(__name__)


class ProcessingPipeline:
    def __init__(
        self,
        worker_id: str,
        repo: DocumentRepository,
        cache: SummaryCache,
        limiter: RateLimiter,
        delay: DelayQueue,
        summarizer: Summarizer,
        settings: Settings,
    ) -> None:
        self._worker_id = worker_id
        self._repo = repo
        self._cache = cache
        self._limiter = limiter
        self._delay = delay
        self._summarizer = summarizer
        self._settings = settings

    @guarded
    @timed
    @log_stage("process_document")
    async def run(self, document: DocumentModel) -> None:
        document_id = str(document.id)
        with bind_context(**document.log_fields()):
            try:
                # The document ref travels with the summary: it is what the
                # template's placeholders are filled from, and what a later
                # cache hit names as the origin of its own text.
                summary = await self._summarizer.summarize(
                    document.title, document.content, document.ref()
                )
            except AppError as exc:
                await self._settle_failure(document, exc)
                return

            completed = await self._repo.complete(document_id, self._worker_id, summary)
            if not completed:
                # The lease was reclaimed while this attempt was running and
                # another worker owns the document now. Drop the result rather
                # than overwrite theirs.
                log.warning("process.lost_lease", document_id=document_id)
                return

            await self._cache.set(document.user_id, document.content_hash, summary)
            await self._release_slot(document)
            log.info("process.completed", document_id=document_id)

    async def _settle_failure(self, document: DocumentModel, exc: AppError) -> None:
        """Retry with backoff while attempts remain, otherwise fail terminally.

        ``attempts`` was incremented when the document was claimed, so it is the
        number of attempts *used*, including this one.
        """
        document_id = str(document.id)
        retriable = exc.transient and document.attempts < self._settings.max_attempts
        error = ProcessingError(
            code=exc.code,
            message=exc.message,
            attempts=document.attempts,
            failed_at=utcnow(),
        )

        if retriable:
            delay_seconds = backoff_delay(
                document.attempts,
                self._settings.retry_base_seconds,
                self._settings.retry_max_seconds,
            )
            requeued = await self._repo.schedule_retry(
                document_id,
                self._worker_id,
                error,
                utcnow() + timedelta(seconds=delay_seconds),
            )
            if requeued:
                # Still queued, so the document keeps its rate-limit slot and
                # keeps its in-flight guard: it is genuinely still in flight.
                await self._delay.schedule(document_id, delay_seconds)
                log.warning(
                    "process.retry",
                    document_id=document_id,
                    attempt=document.attempts,
                    max_attempts=self._settings.max_attempts,
                    error_code=exc.code,
                )
            return

        failed = await self._repo.mark_failed(document_id, self._worker_id, error)
        if failed:
            await self._release_slot(document)
            log.error(
                "process.failed",
                document_id=document_id,
                attempts=document.attempts,
                error_code=exc.code,
                transient=exc.transient,
            )

    async def _release_slot(self, document: DocumentModel) -> None:
        """Terminal state reached: the user gets their capacity back and an
        identical submission may enqueue fresh work again."""
        await self._limiter.release(document.user_id)
        await self._cache.release_inflight(document.user_id, document.content_hash)
