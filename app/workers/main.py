"""Worker process entrypoint: ``python -m app.workers.main``.

The worker is a separate process, not a FastAPI ``BackgroundTasks`` callback.
That is the central async decision in this service, so the reasoning is here
rather than only in the README:

* ``BackgroundTasks`` runs inside the API process, so an API restart or deploy
  kills in-flight work silently -- a 10-30 second job dies mid-flight and the
  client polls a document that will never move;
* it couples capacity to request handling: you cannot add processing capacity
  without adding API capacity, or vice versa;
* it has no redelivery, no ownership and no crash recovery.

A separate process scales independently (``--scale worker=3``), survives API
deploys, and can be drained. The cost is one more moving part and a real queue,
which Redis already provides.
"""

import asyncio
import os
import signal
import uuid

from app.config import get_settings
from app.core.logging import bind_context, configure_logging, get_logger
from app.db import create_mongo_client, create_redis_client, init_models
from app.queue.delay import DelayQueue
from app.queue.stream import DocumentStream
from app.repositories.document import DocumentRepository
from app.services.cache import SummaryCache
from app.services.ratelimit import RateLimiter
from app.services.summarizer import Summarizer
from app.workers.consumer import DocumentConsumer
from app.workers.pipeline import ProcessingPipeline
from app.workers.sweeper import Sweeper

log = get_logger(__name__)


def worker_identity() -> str:
    """Stable within a process, unique across them. The hostname prefix makes a
    Compose replica identifiable in logs and in ``XPENDING`` output."""
    return f"{os.uname().nodename if hasattr(os, 'uname') else 'worker'}-{uuid.uuid4().hex[:8]}"


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    worker_id = worker_identity()
    mongo = create_mongo_client(settings)
    # The blocking XREADGROUP holds the socket for stream_block_seconds by
    # design, so the socket timeout must comfortably outlast it.
    redis = create_redis_client(
        settings,
        socket_timeout=settings.stream_block_seconds + settings.redis_socket_timeout,
    )

    await init_models(mongo, settings)

    repo = DocumentRepository()
    stream = DocumentStream(redis, settings)
    await stream.ensure_group()

    delay = DelayQueue(redis, stream, settings)
    cache = SummaryCache(redis, settings)
    limiter = RateLimiter(redis, repo, settings)
    pipeline = ProcessingPipeline(
        worker_id=worker_id,
        repo=repo,
        cache=cache,
        limiter=limiter,
        delay=delay,
        summarizer=Summarizer(settings),
        settings=settings,
    )
    consumer = DocumentConsumer(worker_id, stream, repo, pipeline, settings)
    sweeper = Sweeper(repo, stream, delay, limiter, settings)

    _install_signal_handlers(consumer, sweeper)

    with bind_context(worker_id=worker_id):
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(consumer.run())
                group.create_task(sweeper.run())
        finally:
            # Draining finished (or the group tore down); close the sockets so
            # the process can exit instead of hanging on open connections.
            await redis.aclose()
            mongo.close()


def _install_signal_handlers(consumer: DocumentConsumer, sweeper: Sweeper) -> None:
    """SIGTERM from ``docker stop`` starts a graceful drain: stop pulling new
    work, let in-flight documents finish, then exit."""
    loop = asyncio.get_running_loop()

    def _shutdown(signame: str) -> None:
        log.info("worker.shutdown_requested", signal=signame)
        consumer.stop()
        sweeper.stop()

    for signame in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _shutdown, signame)
        except NotImplementedError:
            # Windows event loops do not support add_signal_handler; the
            # container runtime is Linux, so this only affects local runs.
            signal.signal(sig, lambda *_: _shutdown(signame))


if __name__ == "__main__":
    asyncio.run(run_worker())
