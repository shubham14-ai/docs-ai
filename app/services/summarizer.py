"""The mock summarizer.

This module exists on its own for one reason: it is the single seam where a real
LLM call would replace the mock. Everything around it -- queueing, claiming,
retrying, caching -- is unchanged by that swap, and the swap is a one-file
change.

The output is deterministic for a given input, which is what makes caching
sound: a cache hit returns exactly what reprocessing would have produced.

The text itself is a **template**. Its placeholders are filled from the document
the summary belongs to, so every summary states which upload it describes, and a
summary served from the cache states that it is a duplicate and names the
original upload that was actually processed. Without that, a cache hit and a
fresh result are indistinguishable once you are looking at the text alone.
"""

import asyncio
import random
import re
from collections import Counter

from app.config import Settings
from app.core.exceptions import TransientProcessingError
from app.core.logging import get_logger
from app.models.document import DocumentRef, Summary, SummarySource

log = get_logger(__name__)

MODEL_NAME = "mock-summarizer-v1"

# Shown where a document detail is genuinely not known: a summary built outside
# the request path, or a cached entry written before summaries carried refs.
PLACEHOLDER = "(unknown)"
NO_PROSE = "(no extractable prose)"

# The one template every summary is rendered through. Nothing else formats
# summary text; a real model would replace the body and keep the envelope.
SUMMARY_TEMPLATE = """{title} - mock summary ({model})

{lead}

Keywords: {keywords}
Length: {word_count} words * {sentence_count} sentences * {char_count} characters
Reading time: ~{reading_time_seconds}s
Document: {doc_id} * user {doc_user} * content {doc_hash} * submitted {doc_submitted}
Provenance: {provenance}"""

GENERATED_PROVENANCE = (
    "generated for this upload; this content was summarized for the first time."
)

DUPLICATE_PROVENANCE = (
    "DUPLICATE UPLOAD - identical content ({doc_hash}) was already summarized, so "
    "this was served from the content cache and never re-processed. "
    'Original: {origin_id} "{origin_title}" submitted {origin_submitted} '
    "by user {origin_user}."
)

# Small, deliberately unclever stop list. A real implementation would not need
# one; this is here so the extracted keywords are not "the", "and", "of".
_STOPWORDS = frozenset(
    """a an and are as at be but by for from has have i if in into is it its of on or
    that the their then there these they this to was were will with you your we our
    not no can could would should do does did been being over under more most such
    """.split()
)

_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_SENTENCE_RE = re.compile(r"[.!?]+(?:\s|$)")

WORDS_PER_MINUTE = 200


class Summarizer:
    def __init__(self, settings: Settings, rng: random.Random | None = None) -> None:
        self._settings = settings
        self._rng = rng or random.Random()

    async def summarize(
        self, title: str, content: str, document: DocumentRef | None = None
    ) -> Summary:
        """Simulate the work, then produce a structured summary.

        The delay is ``asyncio.sleep`` -- I/O-shaped, zero CPU -- which is why a
        single worker process can hold many documents at once (see
        ``app.workers.consumer``).
        """
        await asyncio.sleep(self._simulated_duration())
        self._maybe_fail()
        return self.build_summary(title, content, document)

    def _simulated_duration(self) -> float:
        return self._rng.uniform(
            self._settings.processing_min_seconds,
            self._settings.processing_max_seconds,
        )

    def _maybe_fail(self) -> None:
        """The brief's ~10% random failure.

        Raised as a *transient* error: it is a simulated infrastructure blip, so
        retrying the same content is expected to succeed. A terminal failure
        would be indistinguishable from a bug in the pipeline.
        """
        if self._rng.random() < self._settings.failure_rate:
            raise TransientProcessingError(
                "Simulated summarization failure",
                {"failure_rate": self._settings.failure_rate},
            )

    @classmethod
    def build_summary(
        cls, title: str, content: str, document: DocumentRef | None = None
    ) -> Summary:
        """A fresh summary of ``content``, attributed to ``document``."""
        words = _WORD_RE.findall(content)
        sentences = [s for s in _SENTENCE_RE.split(content) if s.strip()]

        keywords = [
            word
            for word, _ in Counter(
                w.lower() for w in words if len(w) > 3 and w.lower() not in _STOPWORDS
            ).most_common(5)
        ]

        raw_lead = " ".join(sentences[0].split()) if sentences else " ".join(words[:30])
        lead = raw_lead[:280] if raw_lead else ""

        summary = Summary(
            text="",
            lead=lead,
            word_count=len(words),
            char_count=len(content),
            sentence_count=len(sentences),
            keywords=keywords,
            reading_time_seconds=max(1, round(len(words) / WORDS_PER_MINUTE * 60)),
            model=MODEL_NAME,
            source=SummarySource.GENERATED,
            document=document,
        )
        return summary.model_copy(update={"text": cls.render(title, summary)})

    @classmethod
    def as_duplicate(cls, cached: Summary, document: DocumentRef) -> Summary:
        """The cached summary, re-attributed to the upload that just arrived.

        The measurements are carried over untouched -- identical content, so
        recomputing them would produce the same numbers -- and only the
        attribution changes: this upload becomes ``document``, the upload that
        was actually processed becomes ``origin``, and the text is re-rendered
        so the duplication is stated in the summary rather than inferred from a
        flag beside it.

        ``origin`` always names the upload that was *processed*: if the cached
        entry is itself a duplicate, its origin is carried through rather than
        chained, so a copy of a copy still points at the first upload.
        """
        origin = cached.origin or cached.document
        duplicate = cached.model_copy(
            update={
                "source": SummarySource.CACHE,
                "document": document,
                "origin": origin,
            }
        )
        return duplicate.model_copy(
            update={"text": cls.render(document.title, duplicate)}
        )

    # --- rendering ---------------------------------------------------------

    @classmethod
    def render(cls, title: str, summary: Summary) -> str:
        """Fill the template. The only place summary text is composed."""
        if summary.origin is not None:
            provenance = DUPLICATE_PROVENANCE.format(
                doc_hash=cls._short_hash(summary.document),
                **cls._ref_fields(summary.origin, "origin_"),
            )
        else:
            provenance = GENERATED_PROVENANCE

        return SUMMARY_TEMPLATE.format(
            title=title,
            model=summary.model,
            lead=summary.lead or NO_PROSE,
            keywords=", ".join(summary.keywords) or PLACEHOLDER,
            word_count=summary.word_count,
            sentence_count=summary.sentence_count,
            char_count=summary.char_count,
            reading_time_seconds=summary.reading_time_seconds,
            provenance=provenance,
            **cls._ref_fields(summary.document, "doc_"),
        )

    @staticmethod
    def _short_hash(ref: DocumentRef | None) -> str:
        return PLACEHOLDER if ref is None else ref.content_hash[:16]

    @classmethod
    def _ref_fields(cls, ref: DocumentRef | None, prefix: str) -> dict[str, str]:
        """Document details as template placeholders, or ``PLACEHOLDER`` each
        when the reference is missing -- a summary is still renderable without
        one, it simply says so."""
        if ref is None:
            return {
                f"{prefix}{field}": PLACEHOLDER
                for field in ("id", "user", "title", "hash", "submitted")
            }
        return {
            f"{prefix}id": ref.document_id,
            f"{prefix}user": ref.user_id,
            f"{prefix}title": ref.title,
            f"{prefix}hash": cls._short_hash(ref),
            f"{prefix}submitted": ref.submitted_at.isoformat(timespec="seconds"),
        }
