"""Shared fixtures.

Integration tests run against real MongoDB and Redis -- fakes would not prove
anything about the two things that matter here, atomic Lua and Mongo
compare-and-set. They are skipped with a clear message when those are not
reachable, so ``pytest`` still works outside Compose.

Simulated processing durations are configuration, not constants, so the suite
runs in milliseconds instead of the 10-30 seconds a real job takes.
"""

import asyncio
import json
import uuid
from pathlib import Path
from typing import AsyncIterator

import pytest

# ---------------------------------------------------------------------------
# Fixture data loader
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Load a JSON fixture file from tests/fixtures/<name>.json.

    Usage inside a test or parametrize call::

        data = load_fixture("validation")
        @pytest.mark.parametrize("case", load_fixture("validation")["blank_content_cases"])
    """
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from app.config import Settings
from app.db import REDIS_ERRORS
from app.main import create_app
from app.queue.delay import DelayQueue
from app.queue.stream import DocumentStream
from app.repositories.document import DocumentRepository
from app.services.cache import SummaryCache
from app.services.ratelimit import RateLimiter
from app.services.summarizer import Summarizer
from app.workers.pipeline import ProcessingPipeline

CONNECT_TIMEOUT_SECONDS = 3.0


@pytest.fixture(scope="session")
def base_settings() -> Settings:
    """Test settings: an isolated database, no processing delay, no random
    failures. Individual tests re-enable failures where that is the subject."""
    settings = Settings()
    return settings.model_copy(
        update={
            "mongodb_db": f"{settings.mongodb_db}_test",
            "processing_min_seconds": 0.0,
            "processing_max_seconds": 0.0,
            "failure_rate": 0.0,
            "retry_base_seconds": 0.01,
            "retry_max_seconds": 0.05,
            "log_level": "WARNING",
            "log_format": "console",
        }
    )


@pytest.fixture
def settings(base_settings: Settings) -> Settings:
    """Per-test isolation: a unique stream and key namespace, so tests that run
    in the same Redis cannot see each other's messages."""
    suffix = uuid.uuid4().hex[:8]
    return base_settings.model_copy(
        update={
            "stream_key": f"test:stream:{suffix}",
            "retry_zset_key": f"test:retry:{suffix}",
        }
    )


@pytest.fixture
async def mongo(settings: Settings) -> AsyncIterator[AsyncIOMotorClient]:
    from app.db import create_mongo_client, init_models

    client = create_mongo_client(settings)
    try:
        await asyncio.wait_for(
            client.admin.command("ping"), timeout=CONNECT_TIMEOUT_SECONDS
        )
    except Exception as exc:  # noqa: BLE001 - reported as a skip, not swallowed
        client.close()
        pytest.skip(f"MongoDB is not reachable at {settings.mongodb_uri}: {exc}")

    await init_models(client, settings)
    try:
        yield client
    finally:
        await client.drop_database(settings.mongodb_db)
        client.close()


@pytest.fixture
async def redis(settings: Settings) -> AsyncIterator[Redis]:
    from app.db import create_redis_client

    client = create_redis_client(settings)
    try:
        await asyncio.wait_for(client.ping(), timeout=CONNECT_TIMEOUT_SECONDS)
    except (*REDIS_ERRORS, asyncio.TimeoutError) as exc:
        await client.aclose()
        pytest.skip(f"Redis is not reachable at {settings.redis_url}: {exc}")

    await _clear_namespaces(client, settings)
    try:
        yield client
    finally:
        await _clear_namespaces(client, settings)
        await client.aclose()


async def _clear_namespaces(client: Redis, settings: Settings) -> None:
    patterns = [
        "ratelimit:active:*",
        "cache:summary:*",
        "inflight:*",
        settings.stream_key,
        settings.retry_zset_key,
    ]
    for pattern in patterns:
        if "*" in pattern:
            async for key in client.scan_iter(match=pattern, count=500):
                await client.delete(key)
        else:
            await client.delete(pattern)


@pytest.fixture
async def client(
    settings: Settings, mongo: AsyncIOMotorClient, redis: Redis
) -> AsyncIterator[AsyncClient]:
    """An httpx client bound to the real app, lifespan included."""
    app = create_app(settings)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as http_client:
            yield http_client


# --- worker-side fixtures --------------------------------------------------


@pytest.fixture
def repo() -> DocumentRepository:
    return DocumentRepository()


@pytest.fixture
def stream(redis: Redis, settings: Settings) -> DocumentStream:
    return DocumentStream(redis, settings)


@pytest.fixture
def limiter(redis: Redis, repo: DocumentRepository, settings: Settings) -> RateLimiter:
    return RateLimiter(redis, repo, settings)


@pytest.fixture
def cache(redis: Redis, settings: Settings) -> SummaryCache:
    return SummaryCache(redis, settings)


@pytest.fixture
def make_pipeline(
    repo: DocumentRepository,
    cache: SummaryCache,
    limiter: RateLimiter,
    stream: DocumentStream,
    redis: Redis,
    settings: Settings,
):
    """Build a pipeline as a named worker, optionally with settings overrides
    (used to force the failure rate to 1.0 in the failure tests)."""

    def _factory(worker_id: str = "worker-test", **overrides) -> ProcessingPipeline:
        effective = settings.model_copy(update=overrides) if overrides else settings
        return ProcessingPipeline(
            worker_id=worker_id,
            repo=repo,
            cache=cache,
            limiter=RateLimiter(redis, repo, effective),
            delay=DelayQueue(redis, stream, effective),
            summarizer=Summarizer(effective),
            settings=effective,
        )

    return _factory


@pytest.fixture
def submit_payload():
    def _payload(user_id: str = "user-1", title: str = "Quarterly report", content: str | None = None):
        return {
            "user_id": user_id,
            "title": title,
            "content": content or f"Revenue grew. Costs fell. {uuid.uuid4().hex}",
        }

    return _payload
