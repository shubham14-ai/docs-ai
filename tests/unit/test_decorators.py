"""The decorators carry the error-handling policy, so they get direct tests.

The one that matters most: ``@guarded`` must never let an exception disappear.
"""

import asyncio

import pytest

from app.core.decorators import backoff_delay, guarded, retryable
from app.core.exceptions import (
    AppError,
    InternalError,
    TerminalProcessingError,
    TransientProcessingError,
)
from tests.conftest import load_fixture

pytestmark = pytest.mark.unit

_F = load_fixture("decorators")

_FAST = _F["retryable_settings"]["fast"]
_CEIL = _F["retryable_settings"]["ceiling"]


async def test_retryable_retries_transient_errors_until_success():
    calls = 0

    @retryable(**_FAST)
    async def flaky():
        nonlocal calls
        calls += 1
        if calls < _FAST["attempts"]:
            raise TransientProcessingError("blip")
        return "ok"

    assert await flaky() == "ok"
    assert calls == _FAST["attempts"]


@pytest.mark.edge
async def test_retryable_does_not_retry_terminal_errors():
    """Edge: terminal errors must fail fast — not exhaust the retry budget."""
    calls = 0

    @retryable(**_FAST)
    async def broken():
        nonlocal calls
        calls += 1
        raise TerminalProcessingError("bad input")

    with pytest.raises(TerminalProcessingError):
        await broken()
    assert calls == 1, "a terminal error must fail fast, not burn retries"


@pytest.mark.edge
async def test_retryable_gives_up_after_the_attempt_ceiling():
    """Edge: retry ceiling is respected exactly — no infinite loop."""
    calls = 0

    @retryable(**_CEIL)
    async def always_failing():
        nonlocal calls
        calls += 1
        raise TransientProcessingError("still down")

    with pytest.raises(TransientProcessingError):
        await always_failing()
    assert calls == _CEIL["attempts"]


async def test_guarded_wraps_unexpected_exceptions_instead_of_swallowing():
    @guarded
    async def explodes():
        raise ValueError("something nobody anticipated")

    with pytest.raises(InternalError) as caught:
        await explodes()
    assert caught.value.details["exception"] == "ValueError"


async def test_guarded_passes_typed_errors_through_unchanged():
    @guarded
    async def rejects():
        raise TransientProcessingError("known failure")

    with pytest.raises(TransientProcessingError):
        await rejects()


@pytest.mark.edge
async def test_guarded_never_swallows_cancellation():
    """Edge: CancelledError must propagate — swallowing it breaks graceful shutdown."""

    @guarded
    async def cancelled():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await cancelled()


@pytest.mark.parametrize(
    "case",
    _F["backoff_cases"],
    ids=[c["id"] for c in _F["backoff_cases"]],
)
def test_backoff_grows_and_stays_within_its_ceiling(case):
    for attempt in range(1, case["max_attempt"] + 1):
        for _ in range(case["samples"]):
            delay = backoff_delay(attempt, base=case["base"], maximum=case["maximum"])
            assert 0 <= delay <= min(case["maximum"], 2 ** (attempt - 1))


@pytest.mark.edge
def test_backoff_is_jittered():
    """Edge: without jitter, simultaneous failures retry in lockstep forever."""
    jitter = _F["backoff_jitter"]
    delays = {
        backoff_delay(jitter["attempt"], base=jitter["base"], maximum=jitter["maximum"])
        for _ in range(jitter["samples"])
    }
    assert len(delays) >= jitter["min_distinct_values"]


def test_every_domain_error_carries_a_code_and_status():
    for error_cls in AppError.__subclasses__():
        assert error_cls.code
        assert 400 <= error_cls.http_status <= 599
