"""Per-user concurrency limit: at most N documents queued or processing.

Two things make this correct rather than merely present:

**Atomicity.** The check and the increment are one Lua script. ``GET`` then
``INCR`` is a race: two submissions from the same user both read 2, both decide
they are under the limit of 3, and the user ends up with 4 active documents.

**A source of truth.** The Redis counter is an accelerator. MongoDB knows how
many documents are actually active, so if Redis is down the limiter counts from
MongoDB (slower, still correct), and a periodic reconciler repairs drift caused
by a crash between the Mongo write and the Redis update.
"""

from dataclasses import dataclass

from redis.asyncio import Redis

from app.config import Settings
from app.core.logging import get_logger
from app.db import REDIS_ERRORS
from app.repositories.document import DocumentRepository

log = get_logger(__name__)

# Returns {allowed, active}. Atomic: no other command can interleave between the
# read and the increment.
_ACQUIRE_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local limit = tonumber(ARGV[1])
if current >= limit then
  return {0, current}
end
current = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return {1, current}
"""

# Decrement without ever going negative; drop the key at zero so idle users do
# not leave keys behind.
_RELEASE_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current <= 1 then
  redis.call('DEL', KEYS[1])
  return 0
end
current = redis.call('DECR', KEYS[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
return current
"""


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    active: int
    limit: int
    degraded: bool = False

    @property
    def retry_after_seconds(self) -> int:
        """A 429 without Retry-After invites a client hot-loop."""
        return 15


class RateLimiter:
    def __init__(self, redis: Redis, repo: DocumentRepository, settings: Settings) -> None:
        self._redis = redis
        self._repo = repo
        self._settings = settings
        self._acquire = redis.register_script(_ACQUIRE_LUA)
        self._release = redis.register_script(_RELEASE_LUA)

    async def acquire(self, user_id: str) -> RateLimitDecision:
        limit = self._settings.max_active_docs_per_user
        key = self._settings.rate_limit_key(user_id)
        try:
            allowed, active = await self._acquire(
                keys=[key], args=[limit, self._settings.rate_limit_ttl_seconds]
            )
            return RateLimitDecision(bool(allowed), int(active), limit)
        except REDIS_ERRORS as exc:
            return await self._acquire_from_mongo(user_id, limit, exc)

    async def _acquire_from_mongo(
        self, user_id: str, limit: int, exc: BaseException
    ) -> RateLimitDecision:
        """Degraded path: count the truth instead of a counter.

        Not atomic across concurrent submissions, so under a Redis outage two
        simultaneous requests could both pass at the boundary. That is a
        deliberate trade: failing open would remove the limit entirely and
        failing closed would take the whole service down with Redis.
        """
        active = await self._repo.count_active(user_id)
        log.warning(
            "ratelimit.degraded",
            user_id=user_id,
            active=active,
            limit=limit,
            error=str(exc),
        )
        return RateLimitDecision(active < limit, active, limit, degraded=True)

    async def release(self, user_id: str) -> None:
        """Give a slot back. Safe to call more than once for the same document:
        callers only reach here after a Mongo CAS that can succeed exactly once.
        """
        try:
            await self._release(
                keys=[self._settings.rate_limit_key(user_id)],
                args=[self._settings.rate_limit_ttl_seconds],
            )
        except REDIS_ERRORS as exc:
            # Not fatal: the reconciler recomputes counters from MongoDB, so a
            # lost decrement costs a user a slot until the next sweep, not
            # permanently.
            log.warning("ratelimit.release_failed", user_id=user_id, error=str(exc))

    async def reconcile(self) -> int:
        """Recompute every counter from MongoDB. Returns the number repaired.

        This is what stops a crash between "insert document" and "increment
        counter" (or its inverse) from permanently consuming a user's capacity.
        """
        try:
            truth = await self._repo.active_counts_by_user()
            repaired = 0

            for user_id, count in truth.items():
                key = self._settings.rate_limit_key(user_id)
                current = await self._redis.get(key)
                if current is None or int(current) != count:
                    await self._redis.set(
                        key, count, ex=self._settings.rate_limit_ttl_seconds
                    )
                    repaired += 1

            # Counters for users with nothing active must go, or they leak slots.
            async for key in self._redis.scan_iter(match="ratelimit:active:*", count=200):
                user_id = key.rsplit(":", 1)[-1]
                if user_id not in truth:
                    await self._redis.delete(key)
                    repaired += 1

            if repaired:
                log.info("ratelimit.reconciled", repaired=repaired)
            return repaired
        except REDIS_ERRORS as exc:
            log.warning("ratelimit.reconcile_failed", error=str(exc))
            return 0
