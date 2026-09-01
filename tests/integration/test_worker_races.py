"""The race conditions the brief asks about, demonstrated rather than asserted.

These are the most valuable tests in the suite: prose in a README cannot show
that two workers never process the same document, and a unit test with fakes
cannot either -- the guarantee comes from MongoDB's atomic findOneAndUpdate, so
the test has to use a real MongoDB.
"""

import asyncio
from datetime import timedelta

import pytest

from tests import urls
from tests.conftest import load_fixture

from app.models.document import DocumentStatus, utcnow
from app.workers.sweeper import Sweeper

pytestmark = pytest.mark.integration

_F = load_fixture("worker_races")

USER = _F["user"]


async def _submit(client, submit_payload, content: str) -> str:
    response = await client.post(
        urls.documents(), json=submit_payload(user_id=USER, content=content)
    )
    assert response.status_code == 201
    return response.json()["document_id"]


async def test_only_one_of_two_workers_can_claim_a_document(
    client, submit_payload, repo
):
    workers = _F["workers"]
    document_id = await _submit(client, submit_payload, _F["contents"]["contested"])

    claims = await asyncio.gather(
        repo.claim(document_id, workers["a"], 60),
        repo.claim(document_id, workers["b"], 60),
    )

    winners = [c for c in claims if c is not None]
    assert len(winners) == 1, "the status:queued filter must let exactly one through"
    assert winners[0].worker_id in {workers["a"], workers["b"]}
    assert winners[0].attempts == 1, "the losing claim must not inflate attempts"


async def test_ten_simultaneous_claims_still_yield_one_winner(
    client, submit_payload, repo
):
    document_id = await _submit(
        client, submit_payload, _F["contents"]["heavily_contested"]
    )

    claims = await asyncio.gather(
        *(repo.claim(document_id, f"worker-{i}", 60) for i in range(10))
    )

    assert sum(c is not None for c in claims) == 1


async def test_a_redelivered_message_is_not_processed_twice(
    client, submit_payload, repo, make_pipeline
):
    """If an ack is lost the stream redelivers. The claim fails because the
    document is no longer queued, so redelivery is harmless by construction."""
    workers = _F["workers"]
    document_id = await _submit(
        client, submit_payload, _F["contents"]["redelivered"]
    )

    document = await repo.claim(document_id, workers["a"], 60)
    await make_pipeline(workers["a"]).run(document)

    assert await repo.claim(document_id, workers["b"], 60) is None
    stored = await repo.get(document_id)
    assert stored.status is DocumentStatus.COMPLETED
    assert stored.attempts == 1


async def test_a_stale_result_cannot_overwrite_a_reclaimed_document(
    client, submit_payload, repo, make_pipeline
):
    """Worker A stalls, its lease expires, worker B takes over. A's result must
    not land on top of B's document."""
    workers = _F["workers"]
    document_id = await _submit(
        client, submit_payload, _F["contents"]["slow_worker"]
    )
    document = await repo.claim(document_id, workers["a"], 60)

    # Simulate A's lease expiring and B reclaiming the document.
    await repo._collection.update_one(
        {"_id": repo.parse_id(document_id)},
        {"$set": {"lease_expires_at": utcnow() - timedelta(seconds=1)}},
    )
    reclaimed = await repo.reclaim_expired_leases()
    assert reclaimed == [document_id]

    # A finishes late and tries to write its result.
    await make_pipeline(workers["a"]).run(document)

    stored = await repo.get(document_id)
    assert stored.status is DocumentStatus.QUEUED, "A's write must not apply"
    assert stored.worker_id is None


async def test_the_sweeper_requeues_a_document_whose_worker_died(
    client, submit_payload, repo, stream, limiter, settings, redis
):
    sw = _F["sweeper_expected"]
    document_id = await _submit(
        client, submit_payload, _F["contents"]["abandoned"]
    )
    await repo.claim(document_id, _F["workers"]["dying"], 60)
    await repo._collection.update_one(
        {"_id": repo.parse_id(document_id)},
        {"$set": {"lease_expires_at": utcnow() - timedelta(seconds=1)}},
    )

    from app.queue.delay import DelayQueue

    sweeper = Sweeper(
        repo, stream, DelayQueue(redis, stream, settings), limiter, settings
    )
    result = await sweeper.sweep_once()

    assert result["reclaimed"] == sw["reclaimed"]
    stored = await repo.get(document_id)
    assert stored.status is DocumentStatus.QUEUED
    assert stored.lease_expires_at is None


async def test_the_sweeper_repairs_a_drifted_rate_limit_counter(
    client, submit_payload, repo, stream, limiter, settings, redis
):
    """A crash can leave the counter above the truth, silently costing a user
    capacity forever. Reconciliation is what makes that self-healing."""
    await _submit(client, submit_payload, _F["contents"]["one_real"])
    await redis.set(settings.rate_limit_key(USER), _F["drifted_counter"])

    repaired = await limiter.reconcile()

    assert repaired >= 1
    assert int(await redis.get(settings.rate_limit_key(USER))) == 1


async def test_reconciliation_clears_counters_for_users_with_no_active_work(
    limiter, settings, redis, mongo
):
    ghost = _F["ghost_user"]
    await redis.set(settings.rate_limit_key(ghost), _F["ghost_counter"])

    await limiter.reconcile()

    assert await redis.get(settings.rate_limit_key(ghost)) is None
