"""Request and response models.

Validation goes past Pydantic's defaults deliberately — bounded lengths, a
constrained ``user_id`` pattern, bounded pagination, an enum-constrained status
filter. Every rule lives on the model, so it is enforced once and documented
automatically in the OpenAPI schema.
"""

from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.document import DocumentStatus, ProcessingError, Summary

T = TypeVar("T")

# Client-supplied and unauthenticated (see README assumptions). Constrained so
# it cannot become an unbounded key in Redis or an index-bloating Mongo value.
USER_ID_PATTERN = r"^[A-Za-z0-9_.:@-]{1,64}$"


class SubmitDocumentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_id: Annotated[str, Field(pattern=USER_ID_PATTERN, examples=["user-42"])]
    title: Annotated[str, Field(min_length=1, max_length=300)]
    # The upper bound is re-checked against MAX_CONTENT_LENGTH in the service,
    # where settings are available; this bound is the hard ceiling.
    content: Annotated[str, Field(min_length=1, max_length=1_000_000)]

    @field_validator("title", "content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        # min_length runs before stripping for some inputs; a title of "   "
        # must not survive validation.
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class DocumentResponse(BaseModel):
    """Never exposes a raw BSON ObjectId — ``document_id`` is always a string."""

    document_id: str
    user_id: str
    title: str
    status: DocumentStatus
    content_hash: str
    summary: Summary | None = None
    error: ProcessingError | None = None
    attempts: int
    from_cache: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, doc: Any) -> "DocumentResponse":
        return cls(
            document_id=str(doc.id),
            user_id=doc.user_id,
            title=doc.title,
            status=doc.status,
            content_hash=doc.content_hash,
            summary=doc.summary,
            error=doc.error,
            attempts=doc.attempts,
            from_cache=doc.from_cache,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool


class Page(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta


class ListQuery(BaseModel):
    page: Annotated[int, Field(ge=1)] = 1
    page_size: Annotated[int, Field(ge=1, le=100)] = 20
    status: DocumentStatus | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorResponse(BaseModel):
    """One envelope for every error. Never a bare string."""

    error: ErrorBody


class DependencyHealth(BaseModel):
    status: Literal["up", "down"]
    latency_ms: float | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    dependencies: dict[str, DependencyHealth]
