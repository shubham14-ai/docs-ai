"""All MongoDB access for documents.

Nothing outside this module touches the collection. The state transitions are
compare-and-set operations: the filter always includes the status the caller
believes the document is in, so a transition that lost a race simply matches
nothing and returns ``False`` instead of corrupting state.
"""

from datetime import datetime, timedelta
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument

from app.core.logging import get_logger
from app.models.document import (
    DocumentModel,
    DocumentStatus,
    ProcessingError,
    Summary,
    utcnow,
)

log = get_logger(__name__)


class DocumentRepository:
    def __init__(self, model: type[DocumentModel] = DocumentModel) -> None:
        self._model = model

    @property
    def _collection(self) -> Any:
        return self._model.get_motor_collection()

    # --- reads -------------------------------------------------------------

    @staticmethod
    def parse_id(document_id: str) -> ObjectId | None:
        """A malformed id is "not found", never a 500 from ObjectId()."""
        try:
            return ObjectId(document_id)
        except (InvalidId, TypeError):
            return None

    async def get(self, document_id: str) -> DocumentModel | None:
        oid = self.parse_id(document_id)
        if oid is None:
            return None
        return await self._model.get(oid)

    async def list_for_user(
        self,
        user_id: str,
        page: int,
        page_size: int,
        status: DocumentStatus | None = None,
    ) -> tuple[list[DocumentModel], int]:
        query: dict[str, Any] = {"user_id": user_id}
        if status is not None:
            query["status"] = status.value

        total = await self._collection.count_documents(query)
        items = (
            await self._model.find(query)
            .sort("-created_at")
            .skip((page - 1) * page_size)
            .limit(page_size)
            .to_list()
        )
        return items, total

    async def count_active(self, user_id: str) -> int:
        """Rate-limit ground truth. Served by the user_status_created index."""
        return await self._collection.count_documents(
            {
                "user_id": user_id,
                "status": {"$in": [s.value for s in DocumentStatus.active()]},
            }
        )

    async def active_counts_by_user(self) -> dict[str, int]:
        """Per-user active counts, for the counter reconciler."""
        pipeline = [
            {"$match": {"status": {"$in": [s.value for s in DocumentStatus.active()]}}},
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
        ]
        cursor = self._collection.aggregate(pipeline)
        return {row["_id"]: row["count"] async for row in cursor}

    async def find_completed_by_hash(
        self, user_id: str, content_hash: str
    ) -> DocumentModel | None:
        """Rebuild a cache entry from the source of truth after a Redis eviction.

        ``from_cache: False`` restricts this to a document that was genuinely
        processed. Earlier cache hits for the same content are completed
        documents too, and serving one back would make a duplicate's summary
        cite another duplicate as its origin instead of the upload that did the
        work. The oldest such document is the original.
        """
        return (
            await self._model.find(
                {
                    "user_id": user_id,
                    "content_hash": content_hash,
                    "status": DocumentStatus.COMPLETED.value,
                    "summary": {"$ne": None},
                    "from_cache": False,
                }
            )
            .sort("+created_at")
            .first_or_none()
        )

    # --- writes ------------------------------------------------------------

    async def insert(self, document: DocumentModel) -> DocumentModel:
        return await document.insert()

    async def claim(
        self, document_id: str, worker_id: str, lease_seconds: int
    ) -> DocumentModel | None:
        """Atomically take ownership: queued -> processing.

        The ``status: queued`` filter is what makes double-processing impossible.
        If two workers race, exactly one matches; the loser gets ``None`` and
        drops the message.
        """
        oid = self.parse_id(document_id)
        if oid is None:
            return None

        now = utcnow()
        raw = await self._collection.find_one_and_update(
            {"_id": oid, "status": DocumentStatus.QUEUED.value},
            {
                "$set": {
                    "status": DocumentStatus.PROCESSING.value,
                    "worker_id": worker_id,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "updated_at": now,
                },
                "$inc": {"attempts": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if raw is None:
            return None
        return await self._model.get(oid)

    async def complete(
        self, document_id: str, worker_id: str, summary: Summary
    ) -> bool:
        """processing -> completed, only if this worker still holds the lease."""
        return await self._transition(
            document_id,
            expected_status=DocumentStatus.PROCESSING,
            worker_id=worker_id,
            update={
                "$set": {
                    "status": DocumentStatus.COMPLETED.value,
                    "summary": summary.model_dump(),
                    "error": None,
                    "worker_id": None,
                    "lease_expires_at": None,
                    "next_attempt_at": None,
                    "updated_at": utcnow(),
                }
            },
        )

    async def schedule_retry(
        self,
        document_id: str,
        worker_id: str,
        error: ProcessingError,
        next_attempt_at: datetime,
    ) -> bool:
        """processing -> queued, with the next attempt time recorded.

        The document keeps its rate-limit slot: it is still queued, and the
        brief counts queued documents against the limit.
        """
        return await self._transition(
            document_id,
            expected_status=DocumentStatus.PROCESSING,
            worker_id=worker_id,
            update={
                "$set": {
                    "status": DocumentStatus.QUEUED.value,
                    "error": error.model_dump(),
                    "worker_id": None,
                    "lease_expires_at": None,
                    "next_attempt_at": next_attempt_at,
                    "updated_at": utcnow(),
                }
            },
        )

    async def mark_failed(
        self, document_id: str, worker_id: str, error: ProcessingError
    ) -> bool:
        """processing -> failed. Terminal; the rate-limit slot is released."""
        return await self._transition(
            document_id,
            expected_status=DocumentStatus.PROCESSING,
            worker_id=worker_id,
            update={
                "$set": {
                    "status": DocumentStatus.FAILED.value,
                    "error": error.model_dump(),
                    "worker_id": None,
                    "lease_expires_at": None,
                    "next_attempt_at": None,
                    "updated_at": utcnow(),
                }
            },
        )

    async def _transition(
        self,
        document_id: str,
        expected_status: DocumentStatus,
        worker_id: str | None,
        update: dict[str, Any],
    ) -> bool:
        oid = self.parse_id(document_id)
        if oid is None:
            return False
        query: dict[str, Any] = {"_id": oid, "status": expected_status.value}
        if worker_id is not None:
            query["worker_id"] = worker_id
        result = await self._collection.update_one(query, update)
        return result.matched_count == 1

    # --- recovery ----------------------------------------------------------

    async def reclaim_expired_leases(self, limit: int = 100) -> list[str]:
        """Documents whose owner died: processing with an expired lease.

        Each reset is a CAS on the same lease value, so a worker that is merely
        slow and finishes concurrently cannot have its result undone.
        """
        now = utcnow()
        cursor = self._collection.find(
            {
                "status": DocumentStatus.PROCESSING.value,
                "lease_expires_at": {"$lt": now},
            },
            projection={"_id": 1, "lease_expires_at": 1},
        ).limit(limit)

        expired = [row async for row in cursor]
        reclaimed: list[str] = []
        for row in expired:
            result = await self._collection.update_one(
                {
                    "_id": row["_id"],
                    "status": DocumentStatus.PROCESSING.value,
                    "lease_expires_at": row["lease_expires_at"],
                },
                {
                    "$set": {
                        "status": DocumentStatus.QUEUED.value,
                        "worker_id": None,
                        "lease_expires_at": None,
                        "updated_at": now,
                    }
                },
            )
            if result.matched_count == 1:
                reclaimed.append(str(row["_id"]))
        return reclaimed

    async def find_stale_queued(
        self, older_than: datetime, limit: int = 100
    ) -> list[str]:
        """Queued documents nothing has touched -- a stream entry we lost.

        Documents waiting on a scheduled retry are excluded, otherwise the
        sweeper would defeat the retry backoff.
        """
        now = utcnow()
        cursor = self._collection.find(
            {
                "status": DocumentStatus.QUEUED.value,
                "updated_at": {"$lt": older_than},
                "$or": [
                    {"next_attempt_at": None},
                    {"next_attempt_at": {"$lte": now}},
                ],
            },
            projection={"_id": 1},
        ).limit(limit)
        return [str(row["_id"]) async for row in cursor]
