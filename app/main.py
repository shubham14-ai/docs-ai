"""Application factory and lifespan.

Connections are opened once on startup, stored on ``app.state`` and closed on
shutdown. Indexes are ensured and the consumer group is created here, so a fresh
``docker compose up`` needs no manual setup step.

The API never processes a document. It writes to MongoDB and publishes to Redis;
everything else happens in the worker process.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api import errors, meta
from app.api.router import build_api_router
from app.api.v1 import health
from app.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db import create_mongo_client, create_redis_client, init_models
from app.queue.stream import DocumentStream

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.mongo = create_mongo_client(settings)
        app.state.redis = create_redis_client(settings)

        await init_models(app.state.mongo, settings)
        await DocumentStream(app.state.redis, settings).ensure_group()
        log.info("api.started", database=settings.mongodb_db)

        try:
            yield
        finally:
            await app.state.redis.aclose()
            app.state.mongo.close()
            log.info("api.stopped")

    app = FastAPI(
        title="Document Insights API",
        version="1.0.0",
        summary="Submit documents, get summaries produced asynchronously.",
        lifespan=lifespan,
    )

    errors.register(app)
    # Unversioned: operational endpoints, not part of the client contract.
    app.include_router(health.router)
    app.include_router(meta.router)
    # Versioned: every prefix is decided in app/api/router.py.
    app.include_router(build_api_router())
    return app


app = create_app()
