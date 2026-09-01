"""Health check.

It actively pings both dependencies with a short timeout and reports each
separately, and it returns 503 when any of them is down. A health endpoint that
always returns 200 tells an orchestrator nothing, which makes it decoration
rather than a health check.
"""

import asyncio
import time

from fastapi import APIRouter, Response, status

from app.api.deps import MongoDep, RedisDep
from app.schemas.document import DependencyHealth, HealthResponse

router = APIRouter(tags=["health"])

PING_TIMEOUT_SECONDS = 2.0


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness of the service and its dependencies",
    responses={503: {"model": HealthResponse, "description": "A dependency is down"}},
)
async def health(response: Response, mongo: MongoDep, redis: RedisDep) -> HealthResponse:
    mongo_health, redis_health = await asyncio.gather(
        _ping(lambda: mongo.admin.command("ping")),
        _ping(redis.ping),
    )
    dependencies = {"mongodb": mongo_health, "redis": redis_health}

    degraded = any(dep.status == "down" for dep in dependencies.values())
    if degraded:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="degraded" if degraded else "ok", dependencies=dependencies
    )


async def _ping(call) -> DependencyHealth:
    """Time one dependency check.

    Every exception is reported rather than raised -- the whole purpose of this
    endpoint is to answer even when something is broken -- but it is reported
    with its type and message, never swallowed.
    """
    started = time.perf_counter()
    try:
        await asyncio.wait_for(call(), timeout=PING_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return DependencyHealth(status="down", error="ping timed out")
    except Exception as exc:  # noqa: BLE001 - surfaced in the response body
        return DependencyHealth(status="down", error=f"{type(exc).__name__}: {exc}")
    return DependencyHealth(
        status="up", latency_ms=round((time.perf_counter() - started) * 1000, 2)
    )

