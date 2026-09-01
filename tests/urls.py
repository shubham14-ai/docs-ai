"""URL builders for the integration tests.

Tests never spell out a version. ``app/api/router.py`` owns the mapping from a
capability to the version it is served at (see ``docs/01-api-versioning.md``),
so moving a capability to v2 does not touch a single test file.
"""

from app.api.router import API_PREFIX, CAPABILITIES


def _base(capability_name: str) -> str:
    capability = next(c for c in CAPABILITIES if c.name == capability_name)
    router = capability.versions[capability.current]
    return f"{API_PREFIX}/{capability.current}{router.prefix}"


def documents() -> str:
    return _base("documents")


def document(document_id: str) -> str:
    return f"{_base('documents')}/{document_id}"


def user_documents(user_id: str) -> str:
    return f"{_base('users')}/{user_id}/documents"
