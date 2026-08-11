"""
backend/api/main.py
====================
FastAPI application factory and lifespan manager.

Startup sequence:
  1. Configure structured logging
  2. Initialise Redis connection pool
  3. Initialise Qdrant async client + ensure collections exist
  4. Mount all routers

Shutdown sequence:
  1. Drain Redis pool
  2. Close Qdrant client

All middleware is registered here in the correct order (outermost first):
  RequestIDMiddleware → CORS → TrustedHost → GZip → routes
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy.exc import IntegrityError

from backend.api.core.config import settings
from backend.api.core.logging import configure_logging, get_logger
from backend.api.db.qdrant_client import close_qdrant_client, init_qdrant_client
from backend.api.db.redis_client import close_redis_pool, init_redis_pool
from backend.api.middleware.error_handlers import (
    integrity_error_handler,
    unhandled_exception_handler,
    validation_error_handler,
)
from backend.api.middleware.request_id import RequestIDMiddleware
from backend.api.routers import ALL_ROUTERS

# ---------------------------------------------------------------------------
# Logging — configure first so startup logs are structured
# ---------------------------------------------------------------------------
configure_logging()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Sentry (disabled when SENTRY_DSN is empty)
# ---------------------------------------------------------------------------
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.APP_ENV,
        traces_sample_rate=0.2,    # 20% of requests traced
        profiles_sample_rate=0.1,
    )
    logger.info("Sentry initialised", dsn_set=True)


# ---------------------------------------------------------------------------
# Lifespan — manage connection pools that must outlive a single request
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    Everything before `yield` runs at startup;
    everything after runs at shutdown.
    """
    logger.info(
        "Starting Job Autofill Copilot API",
        version=settings.APP_VERSION,
        env=settings.APP_ENV,
    )

    # --- Startup ---
    await init_redis_pool()
    await init_qdrant_client()

    # Bootstrap LlamaIndex global settings (LLM + embedding model)
    from backend.api.agents import configure_llama_settings  # noqa: PLC0415
    configure_llama_settings()

    logger.info("All connections ready — API is live")
    yield

    # --- Shutdown ---
    logger.info("Shutting down — closing connections")
    await close_redis_pool()
    await close_qdrant_client()
    logger.info("Shutdown complete")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """
    Construct and return the configured FastAPI application.
    Separating this into a factory function makes the app testable
    (tests can call create_app() with overridden settings).
    """
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "Job Autofill Copilot — AI-powered job application autofill backend.\n\n"
            "Provides endpoints for resume management, template CRUD, "
            "application history, and the RAG-based answer generation pipeline."
        ),
        docs_url="/docs" if settings.APP_ENV != "production" else None,
        redoc_url="/redoc" if settings.APP_ENV != "production" else None,
        openapi_url="/openapi.json" if settings.APP_ENV != "production" else None,
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------
    # Middleware (applied in reverse order — last added = outermost)
    # ------------------------------------------------------------------

    # 1. GZip — compress responses > 1 KB (innermost, applied first)
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    # 2. CORS — allow browser extension and dashboard origins
    #    NOTE: chrome-extension://* requires we NOT use "*" for origins
    #    when allow_credentials=True; we enumerate specific origins instead.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-ID",
            "X-Extension-Version",   # custom header sent by the Plasmo extension
        ],
        expose_headers=["X-Request-ID"],
    )

    # 3. Trusted hosts — prevent Host header injection in production
    if settings.APP_ENV == "production":
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=["api.autofill-copilot.com", "*.autofill-copilot.com"],
        )

    # 4. Request ID — outermost so every subsequent middleware sees the ID
    app.add_middleware(RequestIDMiddleware)

    # ------------------------------------------------------------------
    # Exception handlers
    # ------------------------------------------------------------------
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(IntegrityError, integrity_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # ------------------------------------------------------------------
    # Routers
    # ------------------------------------------------------------------
    api_prefix = "/api/v1"
    for router, prefix, tags in ALL_ROUTERS:
        app.include_router(
            router,
            prefix=f"{api_prefix}{prefix}",
            tags=tags,
        )
        logger.debug("Router mounted", prefix=f"{api_prefix}{prefix}")

    logger.info("FastAPI app created", routes=len(app.routes))
    return app


# ---------------------------------------------------------------------------
# Application instance — imported by Uvicorn / Gunicorn
# ---------------------------------------------------------------------------
app = create_app()