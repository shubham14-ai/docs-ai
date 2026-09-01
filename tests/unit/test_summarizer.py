"""The mock summarizer, and the simulated failure the brief asks for."""

import random
from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.core.exceptions import TransientProcessingError
from app.models.document import DocumentRef, SummarySource
from app.services.summarizer import Summarizer
from tests.conftest import load_fixture

pytestmark = pytest.mark.unit

_F = load_fixture("summarizer")

CONTENT = _F["content"]


def _settings(**overrides) -> Settings:
    base = {
        "processing_min_seconds": 0.0,
        "processing_max_seconds": 0.0,
        "failure_rate": 0.0,
    }
    return Settings(**{**base, **overrides})


def _ref_from_fixture(key: str) -> DocumentRef:
    r = _F["document_refs"][key]
    return DocumentRef(
        document_id=r["document_id"],
        user_id=r["user_id"],
        title=r["title"],
        content_hash=r["content_hash"],
        submitted_at=datetime.fromisoformat(r["submitted_at"]),
    )


def test_summary_is_deterministic():
    """Caching is only sound because the same input yields the same summary."""
    first = Summarizer.build_summary("Report", CONTENT)
    second = Summarizer.build_summary("Report", CONTENT)
    assert first == second


def test_summary_counts_are_reported():
    summary = Summarizer.build_summary("Report", CONTENT)
    assert summary.word_count == len(CONTENT.split())
    assert summary.char_count == len(CONTENT)
    assert summary.sentence_count == _F["expected_sentence_count"]
    assert summary.reading_time_seconds >= 1


def test_keywords_exclude_stopwords_and_favour_repetition():
    summary = Summarizer.build_summary("Report", CONTENT)
    assert _F["expected_keyword_present"] in summary.keywords
    assert _F["expected_keyword_absent"] not in summary.keywords


@pytest.mark.edge
def test_empty_prose_still_produces_a_summary():
    """Edge: whitespace-only content must not crash — produces zero word_count."""
    summary = Summarizer.build_summary("Empty", "   ")
    assert summary.text
    assert summary.word_count == 0


@pytest.mark.parametrize(
    "case",
    _F["failure_rate_cases"],
    ids=[c["id"] for c in _F["failure_rate_cases"]],
)
async def test_failure_rate_behaviour(case):
    summarizer = Summarizer(
        _settings(failure_rate=case["failure_rate"]),
        rng=random.Random(case["rng_seed"]),
    )
    if case["expect_error"]:
        with pytest.raises(TransientProcessingError):
            await summarizer.summarize("Report", CONTENT)
    else:
        summary = await summarizer.summarize("Report", CONTENT)
        assert summary.model == case["expected_model"]


async def test_simulated_duration_respects_configured_bounds():
    bounds = _F["duration_bounds"]
    settings = _settings(
        processing_min_seconds=bounds["min_seconds"],
        processing_max_seconds=bounds["max_seconds"],
    )
    summarizer = Summarizer(settings, rng=random.Random(bounds["rng_seed"]))
    for _ in range(bounds["sample_count"]):
        assert bounds["min_seconds"] <= summarizer._simulated_duration() <= bounds["max_seconds"]


def test_summary_text_carries_the_document_details():
    """Every template placeholder is filled, so a summary is self-identifying."""
    ref = _ref_from_fixture("original")
    summary = Summarizer.build_summary(ref.title, CONTENT, ref)

    assert summary.source is SummarySource.GENERATED
    assert summary.document == ref
    assert summary.origin is None
    for detail in _F["text_provenance_checks"]:
        assert detail in summary.text
    assert "(unknown)" not in summary.text


@pytest.mark.edge
def test_a_summary_without_a_document_still_renders():
    """Edge: missing DocumentRef must degrade to '(unknown)' placeholders, not KeyError."""
    text = Summarizer.build_summary("Report", CONTENT).text
    assert "(unknown)" in text


def test_a_duplicate_names_the_original_document():
    """The whole point: from the text alone you can tell this upload was a
    duplicate, and which earlier upload produced the summary."""
    original = _ref_from_fixture("original")
    cached = Summarizer.build_summary(original.title, CONTENT, original)

    resubmitted = _ref_from_fixture("resubmitted")
    duplicate = Summarizer.as_duplicate(cached, resubmitted)

    assert duplicate.source is SummarySource.CACHE
    assert duplicate.is_duplicate
    assert duplicate.document == resubmitted
    assert duplicate.origin == original
    assert "DUPLICATE UPLOAD" in duplicate.text
    assert original.document_id in duplicate.text
    assert original.title in duplicate.text
    assert resubmitted.document_id in duplicate.text


def test_a_duplicate_keeps_the_measurements_of_the_original():
    """Only the attribution changes; identical content means identical facts."""
    original = _ref_from_fixture("original")
    cached = Summarizer.build_summary(original.title, CONTENT, original)
    duplicate = Summarizer.as_duplicate(cached, _ref_from_fixture("resubmitted"))

    assert (duplicate.word_count, duplicate.char_count, duplicate.keywords) == (
        cached.word_count,
        cached.char_count,
        cached.keywords,
    )
    assert duplicate.text != cached.text, "the provenance line must differ"


@pytest.mark.edge
def test_a_duplicate_of_a_duplicate_still_points_at_the_first_upload():
    """Edge: origin must not chain — copy-of-copy still cites the processed upload."""
    first = _ref_from_fixture("chain_first")
    cached = Summarizer.build_summary("Report", CONTENT, first)
    second = Summarizer.as_duplicate(cached, _ref_from_fixture("chain_second"))
    third = Summarizer.as_duplicate(second, _ref_from_fixture("chain_third"))

    assert third.origin == first, "the processed upload stays the origin"
    assert first.document_id in second.text
    assert first.document_id in third.text
