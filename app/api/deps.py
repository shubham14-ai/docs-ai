"""Dependency injection providers.

Clients live on ``app.state`` (created once in the lifespan), services are built
per request from them. Nothing constructs its own connection, which is what lets
a test swap Redis or the database by overriding a single provider.
"""

from typing import Annotated

from fastapi import Depends, Request
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from app.config import Settings, get_settings
from app.queue.stream import DocumentStream
from app.repositories.document import DocumentRepository
from app.services.cache import SummaryCache
from app.services.ingestion import IngestionService
from app.services.ratelimit import RateLimiter


def get_config(request: Request) -> Settings:
    # Read from app.state, not the cached factory, so an override in a test
    # lifespan reaches every consumer.
    return getattr(request.app.state, "settings", None) or get_settings()


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_mongo(request: Request) -> AsyncIOMotorClient:
    return request.app.state.mongo


SettingsDep = Annotated[Settings, Depends(get_config)]
RedisDep = Annotated[Redis, Depends(get_redis)]
MongoDep = Annotated[AsyncIOMotorClient, Depends(get_mongo)]


def get_repository() -> DocumentRepository:
    return DocumentRepository()


RepositoryDep = Annotated[DocumentRepository, Depends(get_repository)]


def get_cache(redis: RedisDep, settings: SettingsDep) -> SummaryCache:
    return SummaryCache(redis, settings)


def get_limiter(
    redis: RedisDep, repo: RepositoryDep, settings: SettingsDep
) -> RateLimiter:
    return RateLimiter(redis, repo, settings)


def get_stream(redis: RedisDep, settings: SettingsDep) -> DocumentStream:
    return DocumentStream(redis, settings)


def get_ingestion_service(
    repo: RepositoryDep,
    cache: Annotated[SummaryCache, Depends(get_cache)],
    limiter: Annotated[RateLimiter, Depends(get_limiter)],
    stream: Annotated[DocumentStream, Depends(get_stream)],
    settings: SettingsDep,
) -> IngestionService:
    return IngestionService(repo, cache, limiter, stream, settings)


IngestionDep = Annotated[IngestionService, Depends(get_ingestion_service)]
