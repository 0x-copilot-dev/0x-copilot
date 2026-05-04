"""Logger configuration: JSON formatter + structured logger adapter.

Configuring once at process start replaces stdlib's text formatter with a
JSON-line formatter that emits a fixed schema from ``LogEvent``. The
``StructuredLogger`` adapter is the only ergonomic way to log in app code; it
auto-binds correlation IDs from ``current_context()`` and rejects free-form
``message`` arguments by accepting typed kwargs only. When OpenTelemetry has
configured a tracer, the active span's trace_id and span_id are also attached
to every log record so logs and traces share one correlation ID.
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from typing import Any

from opentelemetry import trace as otel_trace

from backend_app.observability.log_event import (
    LogEvent,
    LogLevel,
    MetadataRedactor,
)
from backend_app.observability.request_context import (
    RequestContext,
    current_context,
)


_SERVICE_NAME = "backend"

_LEVEL_BY_NAME = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_LOG_EVENT_EXTRA_KEY = "log_event"


class JsonLogFormatter(logging.Formatter):
    """Render every log record as a single JSON line.

    Records emitted via ``StructuredLogger`` carry a pre-validated ``LogEvent``
    payload on the ``log_event`` extra. Records emitted via stdlib ``logging``
    (e.g. uvicorn, third-party libs) are wrapped into the same shape with the
    record's level, name, and message text. ``exc_info`` is reduced to
    ``error_class`` plus a list of file:line:func entries -- never the
    exception message, which can include user-supplied content.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = self._extract_payload(record)
        if record.exc_info:
            payload.setdefault(
                "error_class",
                record.exc_info[0].__name__ if record.exc_info[0] else "Exception",
            )
            payload["traceback"] = self._safe_traceback_frames(record.exc_info[2])
        return json.dumps(payload, separators=(",", ":"), default=str)

    @staticmethod
    def _extract_payload(record: logging.LogRecord) -> dict[str, object]:
        event_payload = getattr(record, _LOG_EVENT_EXTRA_KEY, None)
        if isinstance(event_payload, dict):
            return dict(event_payload)
        return {
            "service": _SERVICE_NAME,
            "env": _current_env(),
            "level": record.levelname.lower(),
            "event": record.name,
            "safe_message": record.getMessage(),
        }

    @staticmethod
    def _safe_traceback_frames(tb: object) -> list[str]:
        if tb is None:
            return []
        try:
            frames = traceback.extract_tb(tb)  # type: ignore[arg-type]
        except Exception:
            return []
        return [f"{frame.filename}:{frame.lineno} in {frame.name}" for frame in frames]


class StructuredLogger:
    """Typed logging surface for app code; binds context automatically."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(LogLevel.DEBUG, event, fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit(LogLevel.INFO, event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit(LogLevel.WARNING, event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit(LogLevel.ERROR, event, fields)

    def exception(self, event: str, **fields: Any) -> None:
        self._emit(LogLevel.ERROR, event, fields, exc_info=True)

    def _emit(
        self,
        level: LogLevel,
        event: str,
        fields: dict[str, Any],
        *,
        exc_info: bool = False,
    ) -> None:
        ctx = current_context()
        log_event = self._build_event(level, event, ctx, fields)
        self._logger.log(
            _LEVEL_BY_NAME[level.value],
            event,
            extra={_LOG_EVENT_EXTRA_KEY: log_event.to_log_dict()},
            exc_info=exc_info,
        )

    @staticmethod
    def _build_event(
        level: LogLevel,
        event: str,
        ctx: RequestContext | None,
        fields: dict[str, Any],
    ) -> LogEvent:
        # Allow callers to override context-derived identity (e.g. when emitting
        # from a background task with explicit identity). Otherwise inherit from
        # the bound request context.
        trace_id, span_id = _active_otel_ids()
        return LogEvent(
            service=fields.pop("service", _SERVICE_NAME),
            env=fields.pop("env", _current_env()),
            level=level,
            event=event,
            request_id=fields.pop("request_id", ctx.request_id if ctx else None),
            trace_id=fields.pop("trace_id", trace_id),
            span_id=fields.pop("span_id", span_id),
            org_id=fields.pop("org_id", ctx.org_id if ctx else None),
            user_id=fields.pop("user_id", ctx.user_id if ctx else None),
            method=fields.pop("method", ctx.method if ctx else None),
            route=fields.pop("route", ctx.route if ctx else None),
            status_code=fields.pop("status_code", None),
            duration_ms=fields.pop("duration_ms", None),
            error_class=fields.pop("error_class", None),
            error_code=fields.pop("error_code", None),
            safe_message=fields.pop("safe_message", None),
            metadata=MetadataRedactor.redact(fields.pop("metadata", None) or {}),
        )


def _active_otel_ids() -> tuple[str | None, str | None]:
    """Return (trace_id, span_id) of the active OTEL span if any.

    Returns ``(None, None)`` when no tracer is configured or no span is active.
    """

    span = otel_trace.get_current_span()
    if span is None:
        return None, None
    sc = span.get_span_context()
    if not sc or not sc.is_valid:
        return None, None
    return format(sc.trace_id, "032x"), format(sc.span_id, "016x")


def configure_logging(*, env: str | None = None, level: str | None = None) -> None:
    """Install the JSON formatter on the root logger and set its level.

    Idempotent: re-running clears existing handlers so test harnesses don't
    double up output.
    """

    resolved_level = _resolve_level(level)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved_level)

    # Quiet uvicorn's duplicate access log; ours covers it.
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).disabled = True

    if env is not None:
        os.environ.setdefault("BACKEND_ENVIRONMENT", env)


def get_logger(name: str) -> StructuredLogger:
    """Return a structured logger for the given module name."""

    return StructuredLogger(logging.getLogger(name))


def emit_access_log(
    ctx: RequestContext,
    status: int,
    duration_ms: int,
    error_class: str | None,
) -> None:
    """Default access-log emitter wired into ``RequestContextMiddleware``."""

    logger = get_logger("backend_app.access")
    level = LogLevel.INFO
    if status >= 500 or error_class is not None:
        level = LogLevel.ERROR
    elif status >= 400:
        level = LogLevel.WARNING

    fields: dict[str, Any] = {
        "method": ctx.method,
        "route": ctx.route,
        "status_code": status,
        "duration_ms": duration_ms,
    }
    if error_class is not None:
        fields["error_class"] = error_class

    if level is LogLevel.INFO:
        logger.info("http_request", **fields)
    elif level is LogLevel.WARNING:
        logger.warning("http_request", **fields)
    else:
        logger.error("http_request", **fields)


def _current_env() -> str:
    return (
        os.environ.get("BACKEND_ENVIRONMENT", "development").strip().lower()
        or "development"
    )


def _resolve_level(level: str | None) -> int:
    explicit = (level or os.environ.get("LOG_LEVEL") or "").strip().lower()
    if explicit in _LEVEL_BY_NAME:
        return _LEVEL_BY_NAME[explicit]
    if _current_env() == "production":
        return logging.INFO
    return logging.INFO
