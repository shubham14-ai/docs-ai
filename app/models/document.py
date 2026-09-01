"""The MongoDB document model.

Beanie gives typed access, declarative indexes and Pydantic validation shared
with the API layer, so the stored shape and the wire shape cannot silently drift
apart.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any

import pymongo
from beanie import Document, Indexed
from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

    @classmethod
    def active(cls) -> list["DocumentStatus"]:
        """Statuses that occupy a per-user rate-limit slot."""
        return [cls.QUEUED, cls.PROCESSING]


class SummarySource(str, Enum):
    """How this copy of a summary was obtained."""

    GENERATED = "generated"  # computed by the summarizer for this upload
    CACHE = "cache"  # served from the content cache: a duplicate upload


class DocumentRef(BaseModel):
    """Just enough of a document to identify it inside a summary.

    Embedded in ``Summary`` so a summary carries the upload it was computed for.
    That is what lets a cache hit name *both* documents: the one just submitted
    and the earlier one whose processing actually produced the text.
    """

    document_id: str
    user_id: str
    title: str
    content_hash: str
    submitted_at: datetime


class Summary(BaseModel):
    """The mock summarization result. Structured, not a bare string, so a real
    summarizer can enrich it without breaking the response contract.

    ``text`` is rendered from a template (see ``app.services.summarizer``) whose
    placeholders are filled from ``document`` and ``origin``, so the provenance
    of a summary is legible in the summary itself and not only in the
    ``from_cache`` flag next to it.
    """

    text: str
    lead: str = ""
    word_count: int
    char_count: int
    sentence_count: int
    keywords: list[str] = Field(default_factory=list)
    reading_time_seconds: int
    model: str

    source: SummarySource = SummarySource.GENERATED
    # The upload this copy of the summary belongs to.
    document: DocumentRef | None = None
    # Set only on a duplicate: the earlier upload that was actually processed.
    # ``None`` on a generated summary, which is its own origin.
    origin: DocumentRef | None = None

    @property
    def is_duplicate(self) -> bool:
        return self.source is SummarySource.CACHE


class ProcessingError(BaseModel):
    code: str
    message: str
    attempts: int
    failed_at: datetime


class DocumentModel(Document):
    user_id: Annotated[str, Indexed()]
    title: str
    content: str
    content_hash: str

    status: DocumentStatus = DocumentStatus.QUEUED
    summary: Summary | None = None
    error: ProcessingError | None = None

    attempts: int = 0
    next_attempt_at: datetime | None = None

    # Ownership of an in-flight job. Set on claim, cleared on terminal state.
    worker_id: str | None = None
    lease_expires_at: datetime | None = None

    # True when the summary was served from the content cache rather than
    # computed. Surfaced in the API response; useful in logs and in tests.
    from_cache: bool = False

    schema_version: int = SCHEMA_VERSION
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "documents"
        indexes = [
            # The list endpoint (with and without a status filter) and the
            # rate-limit fallback count. Equality fields first, sort field last,
            # so both queries are served without an in-memory sort.
            pymongo.IndexModel(
                [
                    ("user_id", pymongo.ASCENDING),
                    ("status", pymongo.ASCENDING),
                    ("created_at", pymongo.DESCENDING),
                ],
                name="user_status_created",
            ),
            # Cache-miss reconstruction: rebuild a summary from Mongo when Redis
            # has evicted it, without a collection scan.
            pymongo.IndexModel(
                [("user_id", pymongo.ASCENDING), ("content_hash", pymongo.ASCENDING)],
                name="user_content_hash",
            ),
            # The sweeper: find leases that have expired.
            pymongo.IndexModel(
                [("status", pymongo.ASCENDING), ("lease_expires_at", pymongo.ASCENDING)],
                name="status_lease",
            ),
        ]

    def ref(self) -> DocumentRef:
        """This document as it is referred to from inside a summary."""
        return DocumentRef(
            document_id=str(self.id),
            user_id=self.user_id,
            title=self.title,
            content_hash=self.content_hash,
            submitted_at=self.created_at,
        )

    def touch(self) -> None:
        self.updated_at = utcnow()

    def log_fields(self) -> dict[str, Any]:
        return {
            "document_id": str(self.id),
            "user_id": self.user_id,
            "status": self.status.value,
            "attempts": self.attempts,
        }
