"""
backend/api/core/logging.py
===========================
Structured JSON logging configuration for the entire application.

In development:    human-readable colourised output via structlog
In staging/prod:   JSON log lines consumable by Datadog / CloudWatch / Loki

Every log record automatically carries:
  - timestamp (ISO-8601)
  - log level
  - logger name
  - request_id  (injected by middleware via contextvars)
  - user_id     (injected by auth dependency via contextvars)
"""

import logging
import sys
from contextvars import ContextVar

import structlog

from backend.api.core.config import settings

# ---------------------------------------------------------------------------
# Context variables — set per-request by middleware / dependencies
# ---------------------------------------------------------------------------
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
user_id_var: ContextVar[str] = ContextVar("user_id", default="")


def _add_request_context(
    logger: logging.Logger,       # noqa: ARG001
    method_name: str,             # noqa: ARG001
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Structlog processor: injects request_id and user_id into every log record."""
    rid = request_id_var.get("")
    uid = user_id_var.get("")
    if rid:
        event_dict["request_id"] = rid
    if uid:
        event_dict["user_id"] = uid
    return event_dict


def configure_logging() -> None:
    """
    Call once at application startup (from main.py lifespan).
    Sets up structlog and the standard-library root logger consistently.
    """
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    is_dev = settings.APP_ENV == "development"

    # ---- standard-library handler ----------------------------------------
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )
    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "httpx", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # ---- structlog processors --------------------------------------------
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        _add_request_context,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if is_dev:
        # Pretty console renderer for local development
        renderer = structlog.dev.ConsoleRenderer()
    else:
        # Machine-readable JSON for production log aggregators
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level)


def get_logger(name: str) -> structlog.BoundLogger:
    """Convenience wrapper — use instead of logging.getLogger()."""
    return structlog.get_logger(name)