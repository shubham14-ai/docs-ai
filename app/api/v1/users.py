"""List a user's documents."""

import math
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import RepositoryDep
from app.models.document import DocumentStatus
from app.schemas.document import (
    USER_ID_PATTERN,
    DocumentResponse,
    ErrorResponse,
    ListQuery,
    Page,
    PageMeta,
)

router = APIRouter(prefix="/users", tags=["users"])


def list_query(
    page: Annotated[int, Query(ge=1, description="1-based page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[
        DocumentStatus | None, Query(description="Optional status filter")
    ] = None,
) -> ListQuery:
    """Pagination bounds are enforced here, not in the handler: an unbounded
    page_size is a trivial way to make the service read a whole collection."""
    return ListQuery(page=page, page_size=page_size, status=status)


@router.get(
    "/{user_id}/documents",
    response_model=Page[DocumentResponse],
    summary="List a user's documents, most recent first",
    responses={422: {"model": ErrorResponse, "description": "Bad pagination params"}},
)
async def list_user_documents(
    repo: RepositoryDep,
    user_id: Annotated[str, Path(pattern=USER_ID_PATTERN)],
    query: Annotated[ListQuery, Depends(list_query)],
) -> Page[DocumentResponse]:
    """An unknown user is an empty page, not a 404: absence of documents is not
    absence of a user, and there is no user resource to be missing."""
    items, total = await repo.list_for_user(
        user_id, query.page, query.page_size, query.status
    )
    return Page[DocumentResponse](
        items=[DocumentResponse.from_model(doc) for doc in items],
        meta=PageMeta(
            page=query.page,
            page_size=query.page_size,
            total=total,
            total_pages=math.ceil(total / query.page_size) if total else 0,
            has_next=query.page * query.page_size < total,
        ),
    )
