"""Submit and poll."""

from fastapi import APIRouter, Response, status

from app.api.deps import IngestionDep, RepositoryDep
from app.core.exceptions import DocumentNotFound
from app.schemas.document import (
    DocumentResponse,
    ErrorResponse,
    SubmitDocumentRequest,
)

router = APIRouter(prefix="/documents", tags=["documents"])

COMMON_ERRORS = {
    422: {"model": ErrorResponse, "description": "Validation failed"},
    503: {"model": ErrorResponse, "description": "A dependency is unavailable"},
}


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a document for summarization",
    responses={
        200: {
            "model": DocumentResponse,
            "description": (
                "Returned instead of 201 when no new work was enqueued: the "
                "content was already summarized (served from cache), or an "
                "identical submission is already in flight."
            ),
        },
        409: {
            "model": ErrorResponse,
            "description": "An identical submission is still settling; retry",
        },
        429: {"model": ErrorResponse, "description": "Per-user concurrency limit"},
        **COMMON_ERRORS,
    },
)
async def submit_document(
    payload: SubmitDocumentRequest, response: Response, service: IngestionDep
) -> DocumentResponse:
    """201 when this call created new work, 200 when it did not.

    The distinction is deliberate: a client that batches submissions can tell
    from the status code alone whether it should start polling.
    """
    result = await service.submit(payload.user_id, payload.title, payload.content)
    response.status_code = (
        status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    )
    return DocumentResponse.from_model(result.document)


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Poll a document's processing status",
    responses={404: {"model": ErrorResponse, "description": "Unknown or malformed id"}},
)
async def get_document(document_id: str, repo: RepositoryDep) -> DocumentResponse:
    """A malformed id is a 404, not a 500 from a failed ObjectId construction."""
    document = await repo.get(document_id)
    if document is None:
        raise DocumentNotFound(
            "No document with that id.", {"document_id": document_id}
        )
    return DocumentResponse.from_model(document)
