"""Input validation beyond Pydantic defaults -- a named pitfall in the brief."""

import pytest
from pydantic import ValidationError

from app.models.document import DocumentStatus
from app.schemas.document import ListQuery, SubmitDocumentRequest
from tests.conftest import load_fixture

pytestmark = pytest.mark.unit

_F = load_fixture("validation")


def _payload(**overrides):
    return {**_F["base_payload"], **overrides}


def test_valid_payload_is_accepted():
    request = SubmitDocumentRequest(**_payload())
    assert request.user_id == _F["base_payload"]["user_id"]


@pytest.mark.edge
@pytest.mark.parametrize(
    "content",
    [c["content"] for c in _F["blank_content_cases"]],
    ids=[c["id"] for c in _F["blank_content_cases"]],
)
def test_blank_content_is_rejected(content):
    """Edge: whitespace-only content must be caught after strip, not pass min_length."""
    with pytest.raises(ValidationError):
        SubmitDocumentRequest(**_payload(content=content))


@pytest.mark.edge
def test_blank_title_is_rejected():
    """Edge: whitespace-only title must be rejected post-strip."""
    with pytest.raises(ValidationError):
        SubmitDocumentRequest(**_payload(title="   "))


@pytest.mark.edge
def test_overlong_title_is_rejected():
    """Edge: title at max_length+1 must fail."""
    with pytest.raises(ValidationError):
        SubmitDocumentRequest(**_payload(title="x" * _F["overlong_title_length"]))


@pytest.mark.edge
@pytest.mark.parametrize(
    "user_id",
    [c["user_id"] for c in _F["malformed_user_id_cases"]],
    ids=[c["id"] for c in _F["malformed_user_id_cases"]],
)
def test_malformed_user_ids_are_rejected(user_id):
    """Edge: user_id becomes a Redis key and Mongo index value — must be constrained."""
    with pytest.raises(ValidationError):
        SubmitDocumentRequest(**_payload(user_id=user_id))


@pytest.mark.edge
def test_unknown_fields_are_rejected():
    """Edge: extra='forbid' catches typo'd field names instead of silently dropping them."""
    with pytest.raises(ValidationError):
        SubmitDocumentRequest(**_payload(), priority="high")


def test_content_is_whitespace_stripped():
    strip = _F["content_strip"]
    request = SubmitDocumentRequest(**_payload(content=strip["input"]))
    assert request.content == strip["expected"]


@pytest.mark.edge
@pytest.mark.parametrize(
    "page,page_size",
    [(c["page"], c["page_size"]) for c in _F["bad_pagination_cases"]],
    ids=[c["id"] for c in _F["bad_pagination_cases"]],
)
def test_pagination_bounds_are_enforced(page, page_size):
    """Edge: page=0, negative page, page_size=0, page_size>100 must all be rejected."""
    with pytest.raises(ValidationError):
        ListQuery(page=page, page_size=page_size)


@pytest.mark.edge
def test_status_filter_must_be_a_known_status():
    """Edge: unknown status string must be rejected; valid enum value must round-trip."""
    with pytest.raises(ValidationError):
        ListQuery(status=_F["invalid_status"])
    assert ListQuery(status=_F["valid_status"]).status is DocumentStatus.QUEUED
