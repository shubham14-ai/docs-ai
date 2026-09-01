"""Connection lifecycle for MongoDB and Redis.

Both clients are created once per process and shared. ``tz_aware=True`` on the
Mongo client matters: without it the driver returns naive datetimes and every
comparison against ``datetime.now(timezone.utc)`` raises at runtime.
"""

from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import Settings
from app.core.logging import get_logger
from app.models.document import DocumentModel

log = get_logger(__name__)

# RedisError covers connection, timeout and protocol failures. Every degradation
# path in the service layer catches exactly this -- never a bare Exception.
REDIS_ERRORS = (RedisError, OSError)


def create_mongo_client(settings: Settings) -> AsyncIOMotorClient:
    return AsyncIOMotorClient(
        settings.mongodb_uri,
        tz_aware=True,
        serverSelectionTimeoutMS=5_000,
        uuidRepresentation="standard",
    )


def create_redis_client(
    settings: Settings, socket_timeout: float | None = None
) -> Redis:
    """Build the shared Redis client.

    ``socket_timeout`` is a parameter because the worker needs a longer one than
    the API: it issues ``XREADGROUP ... BLOCK``, which holds the socket open for
    the whole block duration on purpose. A socket timeout shorter than that
    turns every idle poll into a TimeoutError.
    """
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=socket_timeout or settings.redis_socket_timeout,
        socket_connect_timeout=settings.redis_socket_timeout,
        health_check_interval=30,
    )


async def init_models(client: AsyncIOMotorClient, settings: Settings) -> None:
    """Initialise Beanie and ensure indexes.

    Index creation in MongoDB is idempotent, so every process may run this on
    boot without coordination. This is the deliberate substitute for a numbered
    migration runner at this project size -- see README, "What I cut".
    """
    from beanie import init_beanie

    await init_beanie(
        database=client[settings.mongodb_db], document_models=[DocumentModel]
    )
    log.info("mongo.initialised", database=settings.mongodb_db)
