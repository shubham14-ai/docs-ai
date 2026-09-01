"""Content-addressed summary cache, and the in-flight duplicate guard.

The second Redis concern, in its own key namespace. Both operations degrade to
"miss" when Redis is unavailable: a cache that fails closed would take the API
down with it, and a miss only costs a reprocess.

The in-flight guard covers the window a cache alone cannot: between enqueue and
completion nothing is cached yet, so two identical submissions one second apart
would both be processed. ``SET NX`` on ``(user_id, content_hash)`` makes the
second one return the first one's ``document_id`` to poll.
"""

import hashlib

from redis.asyncio import Redis

from app.config import Settings
from app.core.logging import get_logger
from app.db import REDIS_ERRORS
from app.models.document import Summary

log = get_logger(__name__)


def hash_content(content: str) -> str:
    """SHA-256 of the normalized content.

    Normalization decides what "identical" means. Surrounding whitespace and
    line-ending differences are not a meaningful content change -- the same text
    pasted from Windows and from Linux must hit the same cache entry -- so both
    are normalized away. Internal whitespace and case are preserved, because
    those genuinely are different documents.
    """
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class SummaryCache:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    async def get(self, user_id: str, content_hash: str) -> Summary | None:
        key = self._settings.cache_key(user_id, content_hash)
        try:
            raw = await self._redis.get(key)
        except REDIS_ERRORS as exc:
            log.warning("cache.degraded", operation="get", error=str(exc))
            return None
        if raw is None:
            return None
        return Summary.model_validate_json(raw)

    async def set(self, user_id: str, content_hash: str, summary: Summary) -> None:
        key = self._settings.cache_key(user_id, content_hash)
        try:
            await self._redis.set(
                key,
                summary.model_dump_json(),
                ex=self._settings.cache_ttl_seconds,
            )
        except REDIS_ERRORS as exc:
            log.warning("cache.degraded", operation="set", error=str(exc))

    # --- in-flight duplicate guard ----------------------------------------

    async def claim_inflight(
        self, user_id: str, content_hash: str, document_id: str
    ) -> str | None:
        """Reserve this (user, content) pair.

        Returns ``None`` when the reservation was taken, or the ``document_id``
        of the submission already in flight for identical content.
        """
        key = self._settings.inflight_key(user_id, content_hash)
        try:
            acquired = await self._redis.set(
                key, document_id, nx=True, ex=self._settings.inflight_ttl_seconds
            )
            if acquired:
                return None
            existing = await self._redis.get(key)
            # A race with an expiring key leaves nothing to point at; treat that
            # as "no duplicate" and let this submission proceed.
            return existing
        except REDIS_ERRORS as exc:
            log.warning("cache.degraded", operation="claim_inflight", error=str(exc))
            return None

    async def release_inflight(self, user_id: str, content_hash: str) -> None:
        try:
            await self._redis.delete(self._settings.inflight_key(user_id, content_hash))
        except REDIS_ERRORS as exc:
            log.warning("cache.degraded", operation="release_inflight", error=str(exc))
