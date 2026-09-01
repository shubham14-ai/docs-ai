"""Per-user concurrency limiting, including the concurrent case.

The sequential test shows the limit exists. The concurrent test is the one that
matters: it is the only thing that distinguishes an atomic Lua check-and-
increment from a ``GET`` followed by an ``INCR``, which passes the sequential
test and lets four documents through under load.
"""

import asyncio

import pytest

from tests import urls
from tests.conftest import load_fixture

pytestmark = pytest.mark.integration

_F = load_fixture("rate_limit")

USER = _F["user"]


async def test_fourth_concurrent_submission_is_rejected(client, submit_payload):
    exp = _F["rejection_response"]
    for index in range(_F["limit"]):
        response = await client.post(
            urls.documents(),
            json=submit_payload(user_id=USER, content=f"doc {index}"),
        )
        assert response.status_code == 201

    response = await client.post(
        urls.documents(),
        json=submit_payload(user_id=USER, content=f"doc {_F['limit']}"),
    )

    assert response.status_code == exp["expected_status"]
    error = response.json()["error"]
    assert error["code"] == exp["expected_error_code"]
    assert error["details"] == _F["rejection_details"]


async def test_rejection_includes_retry_after_header(client, submit_payload):
    for index in range(_F["limit"]):
        await client.post(
            urls.documents(),
            json=submit_payload(user_id=USER, content=f"doc {index}"),
        )

    response = await client.post(
        urls.documents(),
        json=submit_payload(user_id=USER, content=f"doc {_F['limit']}"),
    )

    assert int(response.headers["Retry-After"]) > 0


async def test_simultaneous_submissions_cannot_exceed_the_limit(
    client, submit_payload
):
    """Six requests in flight at once; exactly three may be accepted."""
    exp = _F["expected_concurrent"]
    responses = await asyncio.gather(
        *(
            client.post(
                urls.documents(),
                json=submit_payload(user_id=USER, content=f"concurrent {index}"),
            )
            for index in range(_F["concurrent_total"])
        )
    )

    codes = [r.status_code for r in responses]
    assert codes.count(201) == exp["accepted"], (
        f"expected exactly {exp['accepted']} accepted, got {codes}"
    )
    assert codes.count(429) == exp["rejected"]


async def test_the_limit_is_per_user(client, submit_payload):
    for index in range(_F["limit"]):
        await client.post(
            urls.documents(),
            json=submit_payload(user_id=USER, content=f"doc {index}"),
        )

    response = await client.post(
        urls.documents(),
        json=submit_payload(user_id=_F["other_user"], content="doc"),
    )

    assert response.status_code == 201


async def test_completing_a_document_frees_the_slot(
    client, submit_payload, repo, make_pipeline
):
    created = [
        (
            await client.post(
                urls.documents(),
                json=submit_payload(user_id=USER, content=f"doc {i}"),
            )
        ).json()
        for i in range(_F["limit"])
    ]
    assert (
        await client.post(
            urls.documents(),
            json=submit_payload(user_id=USER, content="one too many"),
        )
    ).status_code == 429

    pipeline = make_pipeline()
    document = await repo.claim(created[0]["document_id"], "worker-test", 60)
    await pipeline.run(document)

    response = await client.post(
        urls.documents(),
        json=submit_payload(user_id=USER, content="now there is room"),
    )
    assert response.status_code == 201
