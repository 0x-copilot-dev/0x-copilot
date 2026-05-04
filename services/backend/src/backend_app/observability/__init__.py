"""Structured logging and request-context primitives for the backend service.

This module establishes a single logging shape across the service so that no
free-form payloads leak into stdout. Loggers obtained via ``get_logger`` accept
typed fields only; metadata is denylist-redacted and scalar-only. Identity and
correlation IDs are read from a request-scoped ``ContextVar`` populated by
``RequestContextMiddleware``.
"""

from backend_app.observability.log_config import (
    JsonLogFormatter,
    StructuredLogger,
    configure_logging,
    emit_access_log,
    get_logger,
)
from backend_app.observability.log_event import LogEvent, LogLevel
from backend_app.observability.otel import (
    SafeAttributeSpanProcessor,
    TelemetryBootstrap,
)
from backend_app.observability.request_context import (
    RequestContext,
    RequestContextMiddleware,
    current_context,
)

__all__ = [
    "JsonLogFormatter",
    "LogEvent",
    "LogLevel",
    "RequestContext",
    "RequestContextMiddleware",
    "SafeAttributeSpanProcessor",
    "StructuredLogger",
    "TelemetryBootstrap",
    "configure_logging",
    "current_context",
    "emit_access_log",
    "get_logger",
]
