"""Structured logging.

structlog with a JSON renderer in production and a human renderer locally.
Correlation identifiers live in ``contextvars`` and are merged into every event,
so a log line emitted five frames deep still carries ``request_id`` /
``document_id`` without threading a logger through every signature.
"""

import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any, Iterator
from contextlib import contextmanager

import structlog

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_bound: ContextVar[dict[str, Any]] = ContextVar("log_bound", default={})


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO)
    )
    renderer = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _merge_correlation,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _merge_correlation(_logger, _name, event_dict: dict[str, Any]) -> dict[str, Any]:
    rid = _request_id.get()
    if rid:
        event_dict.setdefault("request_id", rid)
    for key, value in _bound.get().items():
        event_dict.setdefault(key, value)
    return event_dict


def new_request_id() -> str:
    return uuid.uuid4().hex


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id() -> str | None:
    return _request_id.get()


@contextmanager
def bind_context(**kwargs: Any) -> Iterator[None]:
    """Bind fields onto every log line emitted inside the block."""
    token = _bound.set({**_bound.get(), **kwargs})
    try:
        yield
    finally:
        _bound.reset(token)


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
