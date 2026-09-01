"""Content-based caching, and the duplicate submission the cache cannot cover."""

import asyncio

import pytest

from tests import urls
from tests.conftest import load_fixture

pytestmark = pytest.mark.integration

_F = load_fixture("caching")

USER = _F["user"]
CONTENT = _F["content"]


async def _process_next(client, submit_payload, repo, make_pipeline, content=CONTENT):
    """Submit and drive the document through one worker cycle."""
    created = (
        await client.post(
            urls.documents(), json=submit_payload(user_id=USER, content=content)
        )
    ).json()
    document = await repo.claim(created["document_id"], "worker-test", 60)
    await make_pipeline().run(document)
    return created


async def test_resubmitting_identical_content_returns_the_summary_immediately(
    client, submit_payload, repo, make_pipeline
):
    exp = _F["cache_hit_response"]
    await _process_next(client, submit_payload, repo, make_pipeline)

    response = await client.post(
        urls.documents(), json=submit_payload(user_id=USER, content=CONTENT)
    )

    assert response.status_code == exp["expected_status"], (
        "a cache hit is not new work, so not a 201"
    )
    body = response.json()
    assert body["status"] == exp["expected_status_value"]
    assert body["from_cache"] is exp["expected_from_cache"]
    assert body["summary"]["text"]


async def test_a_cache_hit_is_marked_as_a_duplicate_of_the_original_upload(
    client, submit_payload, repo, make_pipeline
):
    """``from_cache`` is a flag a reader has to know to look for. The summary
    text is what they are already reading, so the duplication is stated there
    too, along with the upload that was actually processed."""
    dup = _F["duplicate_summary_checks"]
    original = await _process_next(client, submit_payload, repo, make_pipeline)

    body = (
        await client.post(
            urls.documents(),
            json=submit_payload(user_id=USER, title=_F["duplicate_title"], content=CONTENT),
        )
    ).json()

    summary = body["summary"]
    assert summary["source"] == dup["source"]
    assert summary["document"]["document_id"] == body["document_id"]
    assert summary["document"]["title"] == _F["duplicate_title"]
    assert summary["origin"]["document_id"] == original["document_id"]
    assert dup["marker"] in summary["text"]
    assert original["document_id"] in summary["text"]


async def test_a_freshly_processed_summary_names_its_own_document(
    client, submit_payload, repo, make_pipeline
):
    fresh = _F["fresh_summary_checks"]
    created = await _process_next(client, submit_payload, repo, make_pipeline)

    summary = (await client.get(urls.document(created["document_id"]))).json()["summary"]

    assert summary["source"] == fresh["source"]
    assert summary["origin"] is None, "a processed upload is its own origin"
    assert summary["document"]["document_id"] == created["document_id"]
    assert created["document_id"] in summary["text"]
    assert fresh["no_duplicate_marker"] not in summary["text"]


@pytest.mark.edge
async def test_a_duplicate_of_a_duplicate_still_cites_the_processed_upload(
    client, submit_payload, repo, make_pipeline
):
    """Edge: three uploads of the same bytes — origin must not chain through copies."""
    original = await _process_next(client, submit_payload, repo, make_pipeline)

    for _ in range(2):
        body = (
            await client.post(
                urls.documents(), json=submit_payload(user_id=USER, content=CONTENT)
            )
        ).json()
        assert body["summary"]["origin"]["document_id"] == original["document_id"]


async def test_a_cache_hit_does_not_consume_a_rate_limit_slot(
    client, submit_payload, repo, make_pipeline
):
    """No processing capacity is used, so none should be charged."""
    await _process_next(client, submit_payload, repo, make_pipeline)

    for index in range(_F["new_docs_after_cache_hit"]):
        assert (
            await client.post(
                urls.documents(),
                json=submit_payload(user_id=USER, content=f"new {index}"),
            )
        ).status_code == 201

    response = await client.post(
        urls.documents(), json=submit_payload(user_id=USER, content=CONTENT)
    )
    assert response.status_code == _F["cache_hit_response"]["expected_status"]


async def test_the_cache_is_scoped_per_user(
    client, submit_payload, repo, make_pipeline
):
    """A documented isolation decision: one tenant's summary is never served to
    another, even though the content hash is identical."""
    new = _F["new_submission_response"]
    await _process_next(client, submit_payload, repo, make_pipeline)

    response = await client.post(
        urls.documents(),
        json=submit_payload(user_id=_F["other_user"], content=CONTENT),
    )

    assert response.status_code == new["expected_status"]
    assert response.json()["status"] == new["expected_status_value"]


@pytest.mark.edge
async def test_the_cache_survives_redis_eviction(
    client, submit_payload, repo, make_pipeline, redis, settings
):
    """Edge: Redis eviction must fall back to MongoDB and repopulate the cache."""
    exp = _F["cache_hit_response"]
    created = await _process_next(client, submit_payload, repo, make_pipeline)
    document = await repo.get(created["document_id"])
    await redis.delete(settings.cache_key(USER, document.content_hash))

    response = await client.post(
        urls.documents(), json=submit_payload(user_id=USER, content=CONTENT)
    )

    assert response.status_code == exp["expected_status"]
    assert response.json()["from_cache"] is exp["expected_from_cache"]
    assert await redis.exists(settings.cache_key(USER, document.content_hash))


@pytest.mark.edge
async def test_identical_content_submitted_simultaneously_enqueues_once(
    client, submit_payload
):
    """Edge: in-flight duplicate guard collapses N simultaneous identical submissions to 1."""
    exp = _F["concurrent_expected"]
    responses = await asyncio.gather(
        *(
            client.post(
                urls.documents(),
                json=submit_payload(user_id=USER, content=_F["concurrent_content"]),
            )
            for _ in range(_F["concurrent_count"])
        )
    )

    codes = [r.status_code for r in responses]
    assert codes.count(201) == exp["new_count"], (
        f"exactly one submission should enqueue: {codes}"
    )
    assert codes.count(200) == exp["duplicate_count"], (
        "the rest are duplicates, not rejections"
    )

    document_ids = {r.json()["document_id"] for r in responses}
    assert len(document_ids) == 1, "every caller must get the same id to poll"


async def test_a_collapsed_duplicate_does_not_consume_extra_slots(
    client, submit_payload
):
    await asyncio.gather(
        *(
            client.post(
                urls.documents(),
                json=submit_payload(user_id=USER, content=_F["concurrent_content"]),
            )
            for _ in range(_F["concurrent_count"])
        )
    )

    # The in-flight dedup must not have burned the rate-limit slots for the
    # duplicates; 2 fresh submissions should still go through.
    for index in range(2):
        response = await client.post(
            urls.documents(),
            json=submit_payload(user_id=USER, content=f"fresh after race {index}"),
        )
        assert response.status_code == 201
