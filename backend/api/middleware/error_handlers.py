"""
backend/api/middleware/error_handlers.py
=========================================
Centralised FastAPI exception handlers.

Maps common exception types to structured JSON error responses so clients
(extension and dashboard) always receive a consistent error shape:

    {
        "error": {
            "code": "RESOURCE_NOT_FOUND",
            "message": "User not found",
            "request_id": "abc-123"
        }
    }

Handlers registered here are attached to the FastAPI app in main.py.
"""

from __future__ import annotations

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from backend.api.core.logging import get_logger, request_id_var

logger = get_logger(__name__)


def _error_body(code: str, message: str, request_id: str) -> dict:
    return {"error": {"code": code, "message": message, "request_id": request_id}}


def _get_request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or request_id_var.get("") or "unknown"


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic request validation failures."""
    rid = _get_request_id(request)
    errors = exc.errors()
    logger.warning("Request validation error", request_id=rid, errors=errors)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request body or parameters are invalid",
                "details": errors,
                "request_id": rid,
            }
        },
    )


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """Handle database unique constraint violations."""
    rid = _get_request_id(request)
    logger.error("Database integrity error", request_id=rid, error=str(exc.orig))
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=_error_body(
            "CONFLICT",
            "A resource with the same unique identifier already exists.",
            rid,
        ),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unexpected exceptions — log full traceback."""
    rid = _get_request_id(request)
    logger.exception(
        "Unhandled exception",
        request_id=rid,
        path=str(request.url),
        exc_info=exc,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body(
            "INTERNAL_SERVER_ERROR",
            "An unexpected error occurred. Please try again later.",
            rid,
        ),
    )