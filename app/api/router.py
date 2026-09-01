"""Version routing.

Version prefixes are applied *here* and nowhere else. The route modules under
``app/api/v1/`` declare only their resource prefix (``/documents``), so nothing
inside a handler, a schema or a test knows which version it is served under.

The registry is **per capability**, not global. ``documents`` can move to v2
while ``users`` stays on v1; they are separate entries, and neither release is
blocked on the other. That is the whole point -- a single global ``API_VERSION``
forces every endpoint to move together, which in practice means none of them
ever move.

Adding v2 for one capability is:

    1. ``app/api/v2/documents.py`` with the new contract
    2. one line in ``CAPABILITIES`` below
    3. the v1 module stays mounted until its clients are gone

``/health`` is deliberately *not* versioned: it is an operational endpoint for
orchestrators and probes, not part of the API contract clients program against.
Versioning it would mean a liveness probe breaks on an API release.
"""

from dataclasses import dataclass

from fastapi import APIRouter

from app.api.v1 import documents as documents_v1
from app.api.v1 import users as users_v1

API_PREFIX = "/api"


@dataclass(frozen=True)
class Capability:
    """One resource, mounted at one or more versions.

    ``versions`` is ordered newest-first purely for readability; every entry is
    mounted, because a version that is still advertised must still answer.
    """

    name: str
    versions: dict[str, APIRouter]

    @property
    def current(self) -> str:
        return next(iter(self.versions))


CAPABILITIES: tuple[Capability, ...] = (
    Capability("documents", {"v1": documents_v1.router}),
    Capability("users", {"v1": users_v1.router}),
)


def build_api_router() -> APIRouter:
    """Mount every capability at every version it still serves."""
    root = APIRouter()
    for capability in CAPABILITIES:
        for version, router in capability.versions.items():
            root.include_router(router, prefix=f"{API_PREFIX}/{version}")
    return root


def version_map() -> dict[str, str]:
    """The current version of each capability, for ``GET /api/versions``.

    A client that wants to negotiate reads this instead of probing endpoints
    with requests that it expects to fail.
    """
    return {capability.name: capability.current for capability in CAPABILITIES}
