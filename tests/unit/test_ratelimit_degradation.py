"""What the rate limiter does when Redis is gone.

The requirement is graceful degradation, and the interesting question is which
way it degrades. Failing open removes the limit; failing closed takes the API
down with Redis. Counting from MongoDB is slower and still correct, so that is
what it does -- and this test is the proof.
"""

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import Settings
from app.services.ratelimit import RateLimiter, RateLimitDecision
from tests.conftest import load_fixture

pytestmark = pytest.mark.unit

_F = load_fixture("ratelimit_degradation")


class BrokenRedis:
    """Every command raises, as it would with Redis unreachable."""

    def register_script(self, _script):
        async def _raise(*_args, **_kwargs):
            raise RedisConnectionError("connection refused")

        return _raise

    async def get(self, *_args, **_kwargs):
        raise RedisConnectionError("connection refused")

    async def set(self, *_args, **_kwargs):
        raise RedisConnectionError("connection refused")


class StubRepository:
    def __init__(self, active: int) -> None:
        self.active = active
        self.calls = 0

    async def count_active(self, _user_id: str) -> int:
        self.calls += 1
        return self.active


def _limiter(active: int) -> tuple[RateLimiter, StubRepository]:
    settings = Settings(max_active_docs_per_user=_F["limit"])
    repo = StubRepository(active)
    return RateLimiter(BrokenRedis(), repo, settings), repo


@pytest.mark.edge
@pytest.mark.parametrize(
    "case",
    _F["acquire_cases"],
    ids=[c["id"] for c in _F["acquire_cases"]],
)
async def test_acquire_with_broken_redis(case):
    """Edge: rate limiter must degrade to MongoDB count, never fail open or closed."""
    limiter, repo = _limiter(active=case["active"])
    decision = await limiter.acquire("user-1")

    assert decision.allowed is case["expected_allowed"]
    assert decision.degraded is case["expected_degraded"]
    if case["expected_allowed"]:
        assert repo.calls == case["expected_repo_calls"], (
            "the fallback must consult the source of truth"
        )


@pytest.mark.edge
async def test_release_survives_a_redis_outage():
    """Edge: a lost decrement must not propagate — the reconciler repairs counters."""
    limiter, _ = _limiter(active=1)
    await limiter.release("user-1")  # must not raise


def test_rejection_carries_a_retry_after_hint():
    """A 429 with no Retry-After invites the client to hot-loop."""
    r = _F["rejection_retry_after"]
    decision = RateLimitDecision(allowed=False, active=r["active"], limit=r["limit"])
    assert decision.retry_after_seconds > r["expected_min_retry_after_seconds"]
