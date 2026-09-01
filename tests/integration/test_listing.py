"""Pagination and filtering on the list endpoint."""

import pytest

from tests import urls
from tests.conftest import load_fixture

pytestmark = pytest.mark.integration

_F = load_fixture("listing")

USER = _F["user"]


async def _submit_many(client, count: int) -> None:
    """Seed documents directly.

    Going through POST would cap out at the 3-document rate limit after three
    submissions, and these tests are about listing, not about ingestion. The
    ``client`` argument is taken so the app lifespan (and Beanie) is initialised.
    """
    from app.models.document import DocumentModel, DocumentStatus, Summary

    seed = _F["seed_summary"]
    for index in range(count):
        status = (
            DocumentStatus.COMPLETED if index % 2 == 0 else DocumentStatus.QUEUED
        )
        await DocumentModel(
            user_id=USER,
            title=f"Document {index}",
            content=f"content {index}",
            content_hash=f"{index:064d}",
            status=status,
            summary=(
                Summary(
                    text=seed["text"],
                    word_count=seed["word_count"],
                    char_count=seed["char_count"],
                    sentence_count=seed["sentence_count"],
                    keywords=seed["keywords"],
                    reading_time_seconds=seed["reading_time_seconds"],
                    model=seed["model"],
                )
                if status is DocumentStatus.COMPLETED
                else None
            ),
        ).insert()


async def test_listing_is_paginated_and_reports_totals(client):
    sc = _F["paginated_scenario"]
    await _submit_many(client, sc["total_docs"])

    response = await client.get(
        urls.user_documents(USER), params={"page_size": sc["page_size"]}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == sc["expected_items_on_first_page"]
    assert body["meta"] == sc["expected_meta"]


async def test_last_page_reports_no_next(client):
    sc = _F["last_page_scenario"]
    await _submit_many(client, sc["total_docs"])

    body = (
        await client.get(
            urls.user_documents(USER),
            params={"page": sc["page"], "page_size": sc["page_size"]},
        )
    ).json()

    assert len(body["items"]) == sc["expected_items"]
    assert body["meta"]["has_next"] is sc["expected_has_next"]


async def test_status_filter_narrows_the_result(client):
    sc = _F["status_filter_scenario"]
    await _submit_many(client, sc["total_docs"])

    body = (
        await client.get(
            urls.user_documents(USER), params={"status": sc["filter"]}
        )
    ).json()

    assert body["meta"]["total"] == sc["expected_total"]
    assert {item["status"] for item in body["items"]} == set(sc["expected_statuses"])


async def test_documents_are_returned_newest_first(client):
    await _submit_many(client, 5)

    body = (await client.get(urls.user_documents(USER))).json()
    created = [item["created_at"] for item in body["items"]]

    assert created == sorted(created, reverse=True)


@pytest.mark.edge
async def test_a_user_with_no_documents_gets_an_empty_page(client):
    """Edge: unknown user must return an empty page, not a 404."""
    exp = _F["empty_user_expected"]
    body = (await client.get(urls.user_documents(_F["empty_user"]))).json()

    assert body["items"] == exp["items"]
    assert body["meta"]["total"] == exp["total"]
    assert body["meta"]["total_pages"] == exp["total_pages"]


@pytest.mark.parametrize(
    "case",
    _F["invalid_pagination_cases"],
    ids=[c["id"] for c in _F["invalid_pagination_cases"]],
)
async def test_bad_pagination_parameters_are_422(client, case):
    response = await client.get(urls.user_documents(USER), params=case["params"])
    assert response.status_code == case["expected_status"]
