"""Cross-cutting concerns, applied declaratively.

These exist so the pipeline code in ``app/workers`` reads as business steps and
nothing else. Each one is a genuine separation, not decoration:

``@timed``      duration as a log field on every call
``@log_stage``  entry/exit/failure events with bound correlation ids
``@retryable``  exponential backoff with jitter, transient errors only
``@guarded``    wraps unexpected exceptions in a typed error, never swallows

``@guarded`` is the structural reason no ``except Exception: pass`` exists here:
the catch-all is written once, and it re-raises.
"""

import asyncio
import functools
import random
import time
from typing import Any, Awaitable, Callable, TypeVar

from app.core.exceptions import AppError, InternalError
from app.core.logging import bind_context, get_logger

T = TypeVar("T")
AsyncFn = Callable[..., Awaitable[T]]

log = get_logger(__name__)


def timed(fn: AsyncFn) -> AsyncFn:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return await fn(*args, **kwargs)
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            log.debug("stage.timing", stage=fn.__qualname__, duration_ms=elapsed_ms)

    return wrapper


def log_stage(name: str) -> Callable[[AsyncFn], AsyncFn]:
    def decorator(fn: AsyncFn) -> AsyncFn:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with bind_context(stage=name):
                log.debug("stage.start", stage=name)
                try:
                    result = await fn(*args, **kwargs)
                except AppError as exc:
                    log.warning(
                        "stage.failed",
                        stage=name,
                        error_code=exc.code,
                        error=exc.message,
                        transient=exc.transient,
                    )
                    raise
                log.debug("stage.done", stage=name)
                return result

        return wrapper

    return decorator


def retryable(
    attempts: int = 3,
    base_seconds: float = 0.1,
    max_seconds: float = 5.0,
) -> Callable[[AsyncFn], AsyncFn]:
    """Retry *in place*, for short transient blips (a dropped socket, say).

    This is deliberately not the mechanism used for failed document processing:
    a 10-30s job that fails must go back on the queue with a delay so another
    worker can take it and this process is not blocked. See
    ``app.queue.delay.DelayQueue``.
    """

    def decorator(fn: AsyncFn) -> AsyncFn:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except AppError as exc:
                    if not exc.transient or attempt == attempts:
                        raise
                    last = exc
                    delay = backoff_delay(attempt, base_seconds, max_seconds)
                    log.warning(
                        "retry.scheduled",
                        stage=fn.__qualname__,
                        attempt=attempt,
                        max_attempts=attempts,
                        delay_seconds=round(delay, 3),
                        error_code=exc.code,
                    )
                    await asyncio.sleep(delay)
            assert last is not None  # unreachable: loop either returns or raises
            raise last

        return wrapper

    return decorator


def guarded(fn: AsyncFn) -> AsyncFn:
    """Convert anything unexpected into a typed error, with the traceback logged.

    ``asyncio.CancelledError`` is re-raised untouched — swallowing it would break
    graceful shutdown.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except (AppError, asyncio.CancelledError):
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised as a typed error below
            log.exception("stage.unexpected_error", stage=fn.__qualname__)
            raise InternalError(
                f"Unexpected error in {fn.__qualname__}: {exc}",
                {"exception": type(exc).__name__},
            ) from exc

    return wrapper


def backoff_delay(attempt: int, base: float, maximum: float) -> float:
    """Exponential backoff with full jitter.

    Jitter is not optional: without it a burst of simultaneous failures retries
    in lockstep forever, reproducing the thundering herd that caused them.
    """
    ceiling = min(maximum, base * (2 ** (attempt - 1)))
    return random.uniform(0, ceiling)
