"""Unversioned service metadata.

``/api/versions`` reports the current version of each capability so a client can
discover the contract it should call instead of probing endpoints with requests
it expects to 404. It is itself unversioned -- a discovery document that needed
discovering would be circular.
"""

from fastapi import APIRouter

from app.api.router import version_map

router = APIRouter(tags=["meta"])


@router.get(
    "/api/versions",
    response_model=dict[str, str],
    summary="Current API version of each capability",
)
async def versions() -> dict[str, str]:
    return version_map()
