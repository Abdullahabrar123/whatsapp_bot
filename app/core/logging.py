"""Structured JSON logging with correlation IDs and automatic redaction.

A single correlation ID is generated when a webhook arrives and then travels
through the queue, the LLM call and the outbound Meta request, so one customer
interaction can be reconstructed from logs end to end.
"""

from __future__ import annotations

import logging
import sys
import uuid
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from app.core.redaction import redact_value

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def new_correlation_id() -> str:
    """Generate a fresh correlation ID."""
    return uuid.uuid4().hex


def set_correlation_id(value: str | None) -> str:
    """Bind a correlation ID to the current context, generating one if absent."""
    resolved = value or new_correlation_id()
    _correlation_id.set(resolved)
    return resolved


def get_correlation_id() -> str | None:
    """Return the correlation ID bound to the current context, if any."""
    return _correlation_id.get()


def clear_correlation_id() -> None:
    _correlation_id.set(None)


def _add_correlation_id(
    _logger: Any, _name: str, event_dict: EventDict
) -> MutableMapping[str, Any]:
    correlation = _correlation_id.get()
    if correlation:
        event_dict.setdefault("correlation_id", correlation)
    return event_dict


def _redact_processor(
    _logger: Any, _name: str, event_dict: EventDict
) -> MutableMapping[str, Any]:
    """Redact every field of every log line -- defence in depth.

    Callers are expected to pass already-safe values, but a single mistake must
    not leak a token or a full phone number, so redaction is applied centrally.
    """
    return {key: redact_value(key, value) for key, value in event_dict.items()}


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Configure structlog + stdlib logging for the whole process."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_correlation_id,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact_processor,
    ]

    renderer: Processor
    if fmt == "console":
        renderer = structlog.dev.ConsoleRenderer(colors=False)
    else:
        renderer = structlog.processors.JSONRenderer(sort_keys=True)

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
        force=True,
    )
    # Uvicorn's own access log duplicates our structured request log and is not
    # redacted, so it is silenced in favour of the application middleware.
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]
