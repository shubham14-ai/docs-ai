"""What "identical content" means -- the definition the whole cache rests on."""

import pytest

from app.services.cache import hash_content
from tests.conftest import load_fixture

pytestmark = pytest.mark.unit

_F = load_fixture("hashing")


@pytest.mark.parametrize(
    "a,b",
    [(c["a"], c["b"]) for c in _F["identical_pairs"]],
    ids=[c["id"] for c in _F["identical_pairs"]],
)
def test_identical_content_hashes_identically(a, b):
    assert hash_content(a) == hash_content(b)


@pytest.mark.parametrize(
    "a,b",
    [(c["a"], c["b"]) for c in _F["distinct_pairs"]],
    ids=[c["id"] for c in _F["distinct_pairs"]],
)
def test_distinct_content_hashes_differently(a, b):
    assert hash_content(a) != hash_content(b)


@pytest.mark.edge
def test_hash_is_hex_sha256():
    """Edge: output must be exactly 64 lowercase hex characters — not a raw digest."""
    digest = hash_content(_F["sha256_sample"])
    assert len(digest) == 64
    assert all(char in "0123456789abcdef" for char in digest)
