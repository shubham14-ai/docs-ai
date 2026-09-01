"""Exception handlers and the request-id middleware.

Every error leaves this service in one shape:

    {"error": {"code", "message", "details", "request_id"}}

Handlers are registered centrally so no route builds an error response by hand,
and so an unexpected exception cannot escape as an HTML traceback.
"""

import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError, RateLimitExceeded
from app.core.logging import get_logger, get_request_id, new_request_id, set_request_id

log = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: dict | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": get_request_id(),
            }
        },
        headers=headers,
    )


def register(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """Attach a request id to the context, the response and every log line.

        An inbound X-Request-ID is honoured so a correlation id from a gateway
        survives the hop.
        """
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
        set_request_id(request_id)
        started = time.perf_counter()

        response = await call_next(request)

        response.headers[REQUEST_ID_HEADER] = request_id
        log.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return response

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = None
        if isinstance(exc, RateLimitExceeded):
            # Without Retry-After a rejected client will hot-loop.
            retry_after = exc.details.get("retry_after_seconds", 15)
            headers = {"Retry-After": str(retry_after)}
        return error_response(
            exc.http_status, exc.code, exc.message, exc.details, headers
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return error_response(
            422,
            "VALIDATION_FAILED",
            "Request validation failed.",
            {"errors": _serializable_errors(exc)},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return error_response(
            exc.status_code, _code_for(exc.status_code), str(exc.detail)
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # The catch-all is written exactly once, and it logs the traceback and
        # returns a typed body. It never passes silently.
        log.exception("http.unhandled_exception", error=type(exc).__name__)
        return error_response(
            500, "INTERNAL_ERROR", "An unexpected error occurred."
        )


def _serializable_errors(exc: RequestValidationError) -> list[dict]:
    """Strip the original exception objects Pydantic attaches; they are not
    JSON-serializable and leak internals."""
    return [
        {
            "location": list(error.get("loc", [])),
            "message": error.get("msg", ""),
            "type": error.get("type", ""),
        }
        for error in exc.errors()
    ]


def _code_for(status_code: int) -> str:
    return {
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        429: "RATE_LIMIT_EXCEEDED",
    }.get(status_code, "HTTP_ERROR")
