"""The submit flow.

``POST /api/v1/documents`` performs no processing work. It validates, hashes, consults
three Redis guards, writes one MongoDB document and enqueues one stream entry.

The three guards, in order, and why that order:

1. **Content cache** -- identical content already summarized. Return the stored
   summary immediately.
2. **In-flight duplicate guard** -- identical content already being processed.
   The cache cannot cover this window: nothing is cached until processing ends,
   so without this a burst of identical submissions is processed N times.
3. **Rate limit** -- an atomic check-and-increment, last.

The rate limit comes last on purpose. The first two guards both answer "this
submission is not new work", and capacity should only be charged for work that
will actually be performed. Checking the limit first would reject a user at
their limit who re-submits something already cached or already running -- a 429
for capacity the request was never going to use.

Every early exit gives back whatever it had already taken.
"""

import asyncio
from dataclasses import dataclass

from bson import ObjectId

from app.config import Settings
from app.core.decorators import guarded, log_stage
from app.core.exceptions import (
    RateLimitExceeded,
    SubmissionSettling,
    ValidationFailed,
)
from app.core.logging import bind_context, get_logger
from app.models.document import DocumentModel, DocumentStatus, Summary
from app.queue.stream import DocumentStream
from app.repositories.document import DocumentRepository
from app.services.cache import SummaryCache, hash_content
from app.services.ratelimit import RateLimiter
from app.services.summarizer import Summarizer

log = get_logger(__name__)

# How long a duplicate submission waits for the winning request to write its
# document before giving up. Bounded so a request can never hang on it.
SETTLE_ATTEMPTS = 10
SETTLE_DELAY_SECONDS = 0.05


@dataclass(frozen=True)
class SubmitResult:
    document: DocumentModel
    created: bool  # False -> 200 (cache hit or duplicate), True -> 201


class IngestionService:
    def __init__(
        self,
        repo: DocumentRepository,
        cache: SummaryCache,
        limiter: RateLimiter,
        stream: DocumentStream,
        settings: Settings,
    ) -> None:
        self._repo = repo
        self._cache = cache
        self._limiter = limiter
        self._stream = stream
        self._settings = settings

    @guarded
    @log_stage("submit_document")
    async def submit(self, user_id: str, title: str, content: str) -> SubmitResult:
        self._validate(content)
        content_hash = hash_content(content)

        with bind_context(user_id=user_id, content_hash=content_hash[:12]):
            cached = await self._lookup_cached_summary(user_id, content_hash)
            if cached is not None:
                return await self._store_cache_hit(user_id, title, content, content_hash, cached)

            # The id is minted here so it can be published in the in-flight
            # guard before the document exists, which is what lets a racing
            # duplicate be told which document to poll.
            document_id = ObjectId()
            duplicate_id = await self._cache.claim_inflight(
                user_id, content_hash, str(document_id)
            )
            if duplicate_id is not None:
                return await self._join_inflight_duplicate(duplicate_id)

            await self._acquire_slot(user_id, content_hash)

            return await self._enqueue_new(
                document_id, user_id, title, content, content_hash
            )

    # --- steps -------------------------------------------------------------

    def _validate(self, content: str) -> None:
        """The bound that depends on configuration; the rest lives on the
        Pydantic request model where OpenAPI can advertise it."""
        if len(content) > self._settings.max_content_length:
            raise ValidationFailed(
                "Content exceeds the maximum allowed length.",
                {"max_content_length": self._settings.max_content_length,
                 "actual": len(content)},
            )

    async def _lookup_cached_summary(
        self, user_id: str, content_hash: str
    ) -> Summary | None:
        """Redis first; MongoDB as the fallback source of truth.

        A TTL expiry or a Redis restart should not force reprocessing when the
        answer is already stored durably. The Mongo lookup is index-served by
        ``user_content_hash``, and repopulates Redis on the way through.
        """
        summary = await self._cache.get(user_id, content_hash)
        if summary is not None:
            log.info("submit.cache_hit", source="redis")
            return summary

        previous = await self._repo.find_completed_by_hash(user_id, content_hash)
        if previous is not None and previous.summary is not None:
            log.info("submit.cache_hit", source="mongodb")
            # A summary stored before summaries carried refs would leave the
            # duplicate unable to name its origin; the document it was found on
            # is that origin, so attribute it on the way through.
            restored = previous.summary
            if restored.document is None:
                restored = restored.model_copy(update={"document": previous.ref()})
            await self._cache.set(user_id, content_hash, restored)
            return restored

        return None

    async def _store_cache_hit(
        self,
        user_id: str,
        title: str,
        content: str,
        content_hash: str,
        summary: Summary,
    ) -> SubmitResult:
        """A cache hit still creates a document record.

        Documented assumption: each submission stays independently addressable
        and shows up in the user's list, rather than the API handing back an
        older document_id the client never submitted.

        The stored summary is re-attributed before it is written: the new
        document becomes its subject, the document that was actually processed
        becomes its origin, and the text is re-rendered from the template so it
        says outright that this upload was a duplicate and which earlier upload
        it duplicates. ``from_cache`` alone is a flag a reader has to know to
        look for; the summary text is what they are already reading.
        """
        # Minted up front: the id is one of the placeholders the template fills.
        document = DocumentModel(
            id=ObjectId(),
            user_id=user_id,
            title=title,
            content=content,
            content_hash=content_hash,
            status=DocumentStatus.COMPLETED,
            summary=None,
            from_cache=True,
        )
        document.summary = Summarizer.as_duplicate(summary, document.ref())
        await self._repo.insert(document)
        origin = document.summary.origin
        log.info(
            "submit.completed_from_cache",
            document_id=str(document.id),
            origin_document_id=origin.document_id if origin else None,
        )
        return SubmitResult(document=document, created=False)

    async def _acquire_slot(self, user_id: str, content_hash: str) -> None:
        decision = await self._limiter.acquire(user_id)
        if decision.allowed:
            return

        # Nothing was enqueued, so the guard this request took must not outlive
        # it, or the user's next attempt would be answered with an orphan.
        await self._cache.release_inflight(user_id, content_hash)
        log.info("submit.rate_limited", active=decision.active, limit=decision.limit)
        raise RateLimitExceeded(
            f"User already has {decision.active} documents in progress "
            f"(limit {decision.limit}).",
            {
                "limit": decision.limit,
                "active": decision.active,
                "retry_after_seconds": decision.retry_after_seconds,
            },
        )

    async def _join_inflight_duplicate(self, duplicate_id: str) -> SubmitResult:
        """Identical content is already being processed: hand back that id.

        No slot was taken and none is charged -- this submission performs no
        work of its own.

        The guard key is set a few microseconds *before* the winning request
        inserts its document, so a caller that arrives inside that window would
        otherwise look up an id that does not exist yet. Rather than report a
        spurious 404, wait briefly for the insert to land; each await yields to
        the winning request.
        """
        for attempt in range(SETTLE_ATTEMPTS):
            existing = await self._repo.get(duplicate_id)
            if existing is not None:
                log.info("submit.duplicate_inflight", document_id=duplicate_id)
                return SubmitResult(document=existing, created=False)
            if attempt < SETTLE_ATTEMPTS - 1:
                await asyncio.sleep(SETTLE_DELAY_SECONDS)

        # The winner never inserted -- it was rate-limited, or it crashed
        # between SET NX and the insert. The guard key expires on its own TTL;
        # the caller should simply try again.
        log.warning("submit.inflight_unsettled", duplicate_id=duplicate_id)
        raise SubmissionSettling(
            "An identical submission is in progress; retry shortly.",
            {"retry_after_seconds": 1},
        )

    async def _enqueue_new(
        self,
        document_id: ObjectId,
        user_id: str,
        title: str,
        content: str,
        content_hash: str,
    ) -> SubmitResult:
        document = DocumentModel(
            id=document_id,
            user_id=user_id,
            title=title,
            content=content,
            content_hash=content_hash,
            status=DocumentStatus.QUEUED,
        )
        try:
            await self._repo.insert(document)
            await self._stream.publish(str(document_id))
        except Exception:
            # Insert or enqueue failed: undo the slot and the guard so a retry
            # is not rejected for capacity this request never used. The typed
            # error itself propagates untouched to the API error handler.
            await self._limiter.release(user_id)
            await self._cache.release_inflight(user_id, content_hash)
            raise

        log.info("submit.queued", document_id=str(document_id))
        return SubmitResult(document=document, created=True)
