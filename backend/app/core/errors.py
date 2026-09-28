"""Standardized application exceptions and FastAPI exception handlers.

Docs Reference: docs/06 Api Design.md §7, docs/08 Security.md §3.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.core.logging import current_request_id, logger
from backend.app.models.errors import ErrorDetail, ErrorResponse


class AppException(Exception):
    """Base application exception."""

    def __init__(
        self,
        message: str,
        error_type: str = "internal_error",
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        details: Optional[Any] = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.status_code = status_code
        self.details = details


class DBTimeoutError(AppException):
    """Database query statement timeout."""

    def __init__(
        self,
        message: str = "Permintaan membutuhkan waktu terlalu lama untuk diproses oleh database. Silakan persempit filter pencarian Anda.",
    ):
        super().__init__(
            message=message,
            error_type="db_timeout",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class RateLimitExceededError(AppException):
    """Rate limit quota exceeded."""

    def __init__(self, message: str = "Batas laju permintaan terlampaui. Silakan coba kembali sesaat lagi."):
        super().__init__(
            message=message,
            error_type="rate_limit_exceeded",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )


class ASTValidationError(AppException):
    """SQL AST query validation failed."""

    def __init__(self, message: str, details: Optional[Any] = None):
        super().__init__(
            message=message,
            error_type="sql_generation_failed",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            details=details,
        )


def _get_active_request_id(request: Request) -> str:
    """Extract request_id from state, contextvar, header, or generate a fallback."""
    if hasattr(request.state, "request_id") and request.state.request_id:
        return request.state.request_id
    ctx_id = current_request_id.get()
    if ctx_id:
        return ctx_id
    hdr_id = request.headers.get("X-Request-ID")
    if hdr_id:
        return hdr_id
    return str(uuid.uuid4())


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """Handle custom application exceptions."""
    req_id = _get_active_request_id(request)
    logger.warning(
        "Application exception: %s",
        exc.message,
        extra={"request_id": req_id, "error_code": exc.error_type, "status": exc.status_code},
    )
    payload = ErrorResponse(
        request_id=req_id,
        error=ErrorDetail(
            error_type=exc.error_type,
            message=exc.message,
            status_code=exc.status_code,
            details=exc.details,
        ),
    )
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump())


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic request validation errors."""
    req_id = _get_active_request_id(request)
    raw_errors = exc.errors()
    sanitized_errors = jsonable_encoder(raw_errors)

    # Construct clean human-readable messages without leaking internal traces
    msg_parts = []
    for err in raw_errors:
        loc = " -> ".join(str(p) for p in err.get("loc", []))
        msg_parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    message = "Validasi payload permintaan gagal: " + "; ".join(msg_parts)

    logger.info(
        "Request validation failure: %s",
        message,
        extra={"request_id": req_id, "error_code": "validation_error", "status": 422},
    )
    payload = ErrorResponse(
        request_id=req_id,
        error=ErrorDetail(
            error_type="validation_error",
            message=message,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            details=sanitized_errors,
        ),
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=jsonable_encoder(payload.model_dump()),
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handle standard Starlette / FastAPI HTTPExceptions."""
    req_id = _get_active_request_id(request)
    error_type = "http_error"
    if exc.status_code == status.HTTP_404_NOT_FOUND:
        error_type = "not_found"
    elif exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        error_type = "rate_limit_exceeded"
    elif exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
        error_type = "service_unavailable"
    logger.info(
        "HTTP exception: %s -> %d",
        error_type,
        exc.status_code,
        extra={"request_id": req_id, "error_code": error_type, "status": exc.status_code},
    )

    payload = ErrorResponse(
        request_id=req_id,
        error=ErrorDetail(
            error_type=error_type,
            message=str(exc.detail),
            status_code=exc.status_code,
        ),
    )
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump())


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unexpected internal errors (masks raw DB/system error details)."""
    req_id = _get_active_request_id(request)
    logger.error(
        "Unhandled server error: %s",
        str(exc),
        exc_info=True,
        extra={"request_id": req_id, "error_code": "internal_error", "status": 500},
    )
    payload = ErrorResponse(
        request_id=req_id,
        error=ErrorDetail(
            error_type="internal_error",
            message="Terjadi kesalahan internal pada server. Silakan coba kembali beberapa saat lagi.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ),
    )
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=payload.model_dump())


def register_error_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI app instance."""
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
