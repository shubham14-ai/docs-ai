"""The HTTP surface, exercised end to end with httpx.AsyncClient."""

import pytest

from tests import urls
from tests.conftest import load_fixture

pytestmark = pytest.mark.integration

_F = load_fixture("api_documents")


async def test_submit_returns_201_and_a_queued_document(client, submit_payload):
    exp = _F["submit_response"]
    response = await client.post(urls.documents(), json=submit_payload())

    assert response.status_code == exp["expected_status"]
    body = response.json()
    assert body["status"] == exp["expected_status_value"]
    assert body["document_id"]
    assert body["from_cache"] is exp["expected_from_cache"]
    assert len(body["content_hash"]) == exp["expected_content_hash_length"]
    assert body["summary"] is exp["expected_summary"]


async def test_submitted_document_can_be_polled(client, submit_payload):
    created = (await client.post(urls.documents(), json=submit_payload())).json()

    response = await client.get(urls.document(created["document_id"]))

    assert response.status_code == 200
    assert response.json()["document_id"] == created["document_id"]
    assert response.json()["status"] in {"queued", "processing"}


async def test_unknown_document_id_is_404(client):
    nf = _F["not_found"]
    response = await client.get(urls.document(_F["valid_object_id"]))

    assert response.status_code == nf["expected_status"]
    assert response.json()["error"]["code"] == nf["expected_error_code"]


@pytest.mark.edge
async def test_malformed_document_id_is_404_not_500(client):
    """Edge: a bad ObjectId must not surface as a server error — 404, not 500."""
    nf = _F["not_found"]
    response = await client.get(urls.document(_F["malformed_id"]))

    assert response.status_code == nf["expected_status"]
    assert response.json()["error"]["code"] == nf["expected_error_code"]


@pytest.mark.parametrize(
    "case",
    _F["invalid_submit_payloads"],
    ids=[c["id"] for c in _F["invalid_submit_payloads"]],
)
async def test_invalid_payloads_are_422_with_the_error_envelope(client, case):
    response = await client.post(urls.documents(), json=case["payload"])

    assert response.status_code == case["expected_status"]
    error = response.json()["error"]
    assert error["code"] == case["expected_error_code"]
    assert error["request_id"]


async def test_every_response_carries_a_request_id(client, submit_payload):
    response = await client.post(urls.documents(), json=submit_payload())
    assert response.headers["X-Request-ID"]


@pytest.mark.edge
async def test_inbound_request_id_is_preserved(client, submit_payload):
    """Edge: a correlation id from a gateway must survive the hop unchanged."""
    response = await client.post(
        urls.documents(),
        json=submit_payload(),
        headers={"X-Request-ID": _F["correlation_id"]},
    )
    assert response.headers["X-Request-ID"] == _F["correlation_id"]


async def test_health_reports_both_dependencies(client):
    response = await client.get(_F["health_endpoint"])

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["dependencies"]["mongodb"]["status"] == "up"
    assert body["dependencies"]["redis"]["status"] == "up"
    assert body["dependencies"]["mongodb"]["latency_ms"] is not None
