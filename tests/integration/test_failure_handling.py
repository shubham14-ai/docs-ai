"""Failure, retry with backoff, and what a terminal failure releases."""

import pytest

from tests import urls
from tests.conftest import load_fixture

from app.models.document import DocumentStatus

pytestmark = pytest.mark.integration

_F = load_fixture("failure_handling")

USER = _F["user"]
ALWAYS_FAILS = _F["always_fails_settings"]


async def _submit(client, submit_payload, content: str) -> str:
    response = await client.post(
        urls.documents(), json=submit_payload(user_id=USER, content=content)
    )
    assert response.status_code == 201
    return response.json()["document_id"]


async def test_a_failed_attempt_returns_the_document_to_the_queue(
    client, submit_payload, repo, make_pipeline, redis, settings
):
    exp = _F["first_failure"]
    document_id = await _submit(client, submit_payload, _F["documents"]["doomed"])
    pipeline = make_pipeline("worker-a", **ALWAYS_FAILS)

    document = await repo.claim(document_id, "worker-a", 60)
    await pipeline.run(document)

    stored = await repo.get(document_id)
    assert stored.status is DocumentStatus.QUEUED
    assert stored.attempts == exp["expected_attempts"]
    assert stored.error.code == exp["expected_error_code"]
    assert stored.next_attempt_at is not None, "the retry must be delayed, not immediate"
    assert await redis.zcard(settings.retry_zset_key) == exp["expected_retry_zset_size"]


async def test_a_retry_still_holds_its_rate_limit_slot(
    client, submit_payload, repo, make_pipeline, redis, settings
):
    """A queued document counts against the limit, exactly as the brief words
    it -- a failed attempt must not hand back capacity it is still using."""
    document_id = await _submit(client, submit_payload, _F["documents"]["doomed"])
    document = await repo.claim(document_id, "worker-a", 60)

    await make_pipeline("worker-a", **ALWAYS_FAILS).run(document)

    assert int(await redis.get(settings.rate_limit_key(USER))) == 1


async def test_attempts_are_exhausted_then_the_document_fails(
    client, submit_payload, repo, make_pipeline, redis, settings
):
    exp = _F["terminal_failure"]
    document_id = await _submit(client, submit_payload, _F["documents"]["doomed"])
    pipeline = make_pipeline("worker-a", **ALWAYS_FAILS)

    for _ in range(settings.max_attempts):
        document = await repo.claim(document_id, "worker-a", 60)
        assert document is not None
        await pipeline.run(document)

    stored = await repo.get(document_id)
    assert stored.status is DocumentStatus.FAILED
    assert stored.attempts == settings.max_attempts
    assert stored.error.attempts == settings.max_attempts


@pytest.mark.edge
async def test_a_terminal_failure_releases_the_slot_and_the_duplicate_guard(
    client, submit_payload, repo, make_pipeline, redis, settings
):
    """Edge: after max failures, slot AND inflight guard must both be released."""
    document_id = await _submit(client, submit_payload, _F["documents"]["doomed"])
    document = await repo.get(document_id)
    pipeline = make_pipeline("worker-a", **ALWAYS_FAILS)

    for _ in range(settings.max_attempts):
        claimed = await repo.claim(document_id, "worker-a", 60)
        await pipeline.run(claimed)

    assert await redis.get(settings.rate_limit_key(USER)) is None
    assert not await redis.exists(settings.inflight_key(USER, document.content_hash))

    response = await client.post(
        urls.documents(),
        json=submit_payload(user_id=USER, content=_F["documents"]["fresh_start"]),
    )
    assert response.status_code == 201


async def test_a_failed_document_is_reported_through_the_api(
    client, submit_payload, repo, make_pipeline, settings
):
    exp = _F["api_failed_response"]
    document_id = await _submit(client, submit_payload, _F["documents"]["doomed"])
    pipeline = make_pipeline("worker-a", **ALWAYS_FAILS)
    for _ in range(settings.max_attempts):
        await pipeline.run(await repo.claim(document_id, "worker-a", 60))

    body = (await client.get(urls.document(document_id))).json()

    assert body["status"] == exp["expected_status_value"]
    assert body["summary"] is exp["expected_summary"]
    assert body["error"]["code"] == exp["expected_error_code"]
    assert body["attempts"] == settings.max_attempts


async def test_due_retries_are_promoted_back_onto_the_stream(
    client, submit_payload, repo, make_pipeline, stream, redis, settings
):
    from app.queue.delay import DelayQueue

    exp = _F["promote_due"]
    document_id = await _submit(client, submit_payload, _F["documents"]["doomed"])
    # Zero backoff so the entry is due the instant it is scheduled -- the point
    # under test is the atomic pop-and-publish, not the delay itself.
    pipeline = make_pipeline(
        "worker-a", failure_rate=1.0, retry_base_seconds=0.0, retry_max_seconds=0.0
    )
    await pipeline.run(await repo.claim(document_id, "worker-a", 60))

    delay = DelayQueue(redis, stream, settings)
    promoted = await delay.promote_due()

    assert promoted == exp["expected_promoted"]
    assert await redis.zcard(settings.retry_zset_key) == exp["expected_zset_after"]
    entries = await stream.read(consumer="reader", count=10, block_ms=10)
    assert document_id in {entry.document_id for entry in entries}
