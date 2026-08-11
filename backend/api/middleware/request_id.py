"""
backend/api/middleware/request_id.py
=====================================
ASGI middleware that assigns a unique request ID to every incoming request.

The ID is:
  - Taken from the X-Request-ID header if the caller provides one
    (allows distributed tracing across services/extension)
  - Otherwise generated as a UUID4

The ID is:
  1. Stored in the structlog context var (auto-enriches every log line)
  2. Added to the response headers as X-Request-ID (for client correlation)
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from backend.api.core.logging import request_id_var


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique request ID to every request/response cycle."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # Honour caller-provided ID (e.g. from the browser extension)
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        # Push into structlog context — all log lines in this request carry it
        token = request_id_var.set(request_id)

        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)

        # Echo the ID in the response so clients can correlate logs
        response.headers["X-Request-ID"] = request_id
        return response