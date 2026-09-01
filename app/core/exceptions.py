"""Typed domain errors.

Every failure in this codebase is one of these, or it is a bug. Two properties
matter:

* each carries a stable machine-readable ``code`` and an HTTP status, so the API
  error envelope is built mechanically rather than by hand at each raise site;
* each is classified transient or terminal, so the retry decorator can decide
  without inspecting messages.

There is no ``except Exception: pass`` anywhere. Unexpected exceptions are
converted by ``@guarded`` into ``InternalError`` and logged with a traceback.
"""

from typing import Any


class AppError(Exception):
    """Base class for every error this service raises deliberately."""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500
    transient: bool = False

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


# --- API-surface errors ----------------------------------------------------


class DocumentNotFound(AppError):
    code = "DOCUMENT_NOT_FOUND"
    http_status = 404


class ValidationFailed(AppError):
    code = "VALIDATION_FAILED"
    http_status = 422


class RateLimitExceeded(AppError):
    code = "RATE_LIMIT_EXCEEDED"
    http_status = 429


class SubmissionSettling(AppError):
    """An identical submission is mid-flight but its document is not written yet.

    A genuine conflict rather than a client error: the request is valid, and the
    same request a moment later will succeed. 409 says exactly that.
    """

    code = "SUBMISSION_SETTLING"
    http_status = 409
    transient = True


class DependencyUnavailable(AppError):
    """A backing store we cannot degrade around (e.g. cannot enqueue work)."""

    code = "DEPENDENCY_UNAVAILABLE"
    http_status = 503
    transient = True


# --- Worker-side errors ----------------------------------------------------


class TransientProcessingError(AppError):
    """Retryable: the same input may well succeed on a later attempt."""

    code = "PROCESSING_TRANSIENT"
    http_status = 500
    transient = True


class TerminalProcessingError(AppError):
    """Not retryable: retrying this input will fail identically."""

    code = "PROCESSING_TERMINAL"
    http_status = 500


class InternalError(AppError):
    """An unexpected exception, wrapped so it can never be silently dropped."""

    code = "INTERNAL_ERROR"
    http_status = 500
