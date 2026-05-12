"""HTTP-scope structured logging for the runtime API + worker.

The existing ``RuntimeLogger`` / ``RuntimeLogEvent`` is per-run scoped: every
record requires a ``run_id`` and a ``trace_id``. HTTP requests don't have a
run_id at ingress, so this module adds an HTTP-scope ``HttpLogEvent`` plus an
ASGI middleware that binds ``request_id`` / ``org_id`` / ``user_id`` to a
``ContextVar`` for the lifetime of a request. The same JSON formatter
serializes both event shapes, so a single stdout stream covers run + request
logs without losing structure.

Per the service rule, helpers live inside classes; module-level state is
limited to the ContextVar and constants.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from enum import StrEnum
import json
import logging
import os
import time
import traceback
import warnings
from typing import Any, ClassVar
import uuid

from enterprise_service_contracts.headers import (
    ORG_HEADER,
    REQUEST_ID_HEADER,
    USER_HEADER,
)
from opentelemetry import trace as otel_trace
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_runtime.observability.redactor import MetadataRedactor, SafeLogDumper


_SERVICE_NAME = "ai-backend"
_LOG_EVENT_EXTRA_KEY = "log_event"
_RUNTIME_EVENT_EXTRA_KEY = "runtime"
_LEVEL_BY_NAME = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def _active_otel_ids() -> tuple[str | None, str | None]:
    """Return (trace_id, span_id) of the active OTEL span if any.

    Returns ``(None, None)`` when no tracer is configured or no span is
    active. Module-level for symmetry with the sibling backend services.
    """

    span = otel_trace.get_current_span()
    if span is None:
        return None, None
    sc = span.get_span_context()
    if not sc or not sc.is_valid:
        return None, None
    return format(sc.trace_id, "032x"), format(sc.span_id, "016x")


class HttpLogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class HttpLogEvent(BaseModel):
    """Structured log record for HTTP-scope and process-scope events."""

    model_config = ConfigDict(extra="forbid")

    service: str = Field(min_length=1)
    env: str = Field(min_length=1)
    level: HttpLogLevel = HttpLogLevel.INFO
    event: str = Field(min_length=1)
    request_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    org_id: str | None = None
    user_id: str | None = None
    method: str | None = None
    route: str | None = None
    status_code: int | None = Field(default=None, ge=0, le=599)
    duration_ms: int | None = Field(default=None, ge=0)
    error_class: str | None = None
    error_code: str | None = None
    safe_message: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def _redact_metadata(cls, value: object) -> dict[str, object]:
        return MetadataRedactor.redact(value)

    def to_log_dict(self) -> dict[str, object]:
        # Route through ``SafeLogDumper`` so any field annotated ``Sensitive(...)``
        # is elided. No current ``HttpLogEvent`` field is tagged — the integration
        # is in place for future taggings.
        return SafeLogDumper.dump_safe(self, mode="json", exclude_none=True)


class HttpRequestContext(BaseModel):
    """Identity + correlation IDs for one in-flight HTTP request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    org_id: str | None = None
    user_id: str | None = None
    method: str | None = None
    route: str | None = None


class HttpRequestContextHolder:
    """Class-scoped ``ContextVar`` for the in-flight HTTP request context."""

    _VAR: ClassVar[ContextVar[HttpRequestContext | None]] = ContextVar(
        "ai_backend_http_request_context", default=None
    )

    @classmethod
    def get(cls) -> HttpRequestContext | None:
        return cls._VAR.get()

    @classmethod
    def set(cls, ctx: HttpRequestContext) -> Token:
        return cls._VAR.set(ctx)

    @classmethod
    def reset(cls, token: Token) -> None:
        cls._VAR.reset(token)


class JsonLogFormatter(logging.Formatter):
    """Serialize records emitted by either ``RuntimeLogger`` or the HTTP logger.

    A record carries a payload on either the ``log_event`` extra (HTTP path) or
    the ``runtime`` extra (existing ``RuntimeLogger``); both are dicts already
    redacted by their Pydantic models. Records from third-party libs that lack
    a structured payload are wrapped into a minimal shape so stdout stays
    JSON-only. ``exc_info`` is reduced to ``error_class`` plus a list of
    file:line:func entries -- never the exception message text.
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
        for key in (_LOG_EVENT_EXTRA_KEY, _RUNTIME_EVENT_EXTRA_KEY):
            event_payload = getattr(record, key, None)
            if isinstance(event_payload, dict):
                return dict(event_payload)
        return {
            "service": _SERVICE_NAME,
            "env": LoggingConfigurator.current_env(),
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


class HttpStructuredLogger:
    """Typed logging surface for HTTP-scope code; binds context automatically."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(HttpLogLevel.DEBUG, event, fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit(HttpLogLevel.INFO, event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit(HttpLogLevel.WARNING, event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit(HttpLogLevel.ERROR, event, fields)

    def exception(self, event: str, **fields: Any) -> None:
        self._emit(HttpLogLevel.ERROR, event, fields, exc_info=True)

    def _emit(
        self,
        level: HttpLogLevel,
        event: str,
        fields: dict[str, Any],
        *,
        exc_info: bool = False,
    ) -> None:
        ctx = HttpRequestContextHolder.get()
        trace_id, span_id = _active_otel_ids()
        log_event = HttpLogEvent(
            service=_SERVICE_NAME,
            env=LoggingConfigurator.current_env(),
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
        self._logger.log(
            _LEVEL_BY_NAME[level.value],
            event,
            extra={_LOG_EVENT_EXTRA_KEY: log_event.to_log_dict()},
            exc_info=exc_info,
        )


class LoggingConfigurator:
    """Process-wide JSON-logging setup used by both the API and worker."""

    @classmethod
    def configure(cls, *, env: str | None = None, level: str | None = None) -> None:
        resolved_level = cls._resolve_level(level)
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())

        root = logging.getLogger()
        root.handlers.clear()
        root.addHandler(handler)
        root.setLevel(resolved_level)

        for noisy in ("uvicorn.access",):
            logging.getLogger(noisy).disabled = True

        # Pydantic v2 emits one UserWarning per non-matching variant when the
        # OpenAI SDK's `ParsedResponse[T]` (a parameterized member of a
        # discriminated union) is serialized — every Responses-API call
        # downstream of LangChain's structured-output adapter spams the log.
        # Suppress narrowly so other Pydantic UserWarnings still surface.
        warnings.filterwarnings(
            "ignore",
            category=UserWarning,
            module="pydantic.main",
            message=r".*Pydantic serializer warnings:.*PydanticSerializationUnexpectedValue.*",
        )

        if env is not None:
            os.environ.setdefault("RUNTIME_ENVIRONMENT", env)

    @classmethod
    def get_logger(cls, name: str) -> HttpStructuredLogger:
        return HttpStructuredLogger(logging.getLogger(name))

    @staticmethod
    def current_env() -> str:
        value = os.environ.get("RUNTIME_ENVIRONMENT", "development").strip().lower()
        return value or "development"

    @classmethod
    def _resolve_level(cls, level: str | None) -> int:
        explicit = (level or os.environ.get("LOG_LEVEL") or "").strip().lower()
        if explicit in _LEVEL_BY_NAME:
            return _LEVEL_BY_NAME[explicit]
        return logging.INFO


class HttpAccessLogEmitter:
    """Emit one access log line per request with status-driven severity."""

    _LOGGER_NAME = "agent_runtime.http.access"

    @classmethod
    def emit(
        cls,
        ctx: HttpRequestContext,
        *,
        status: int,
        duration_ms: int,
        error_class: str | None,
    ) -> None:
        logger = LoggingConfigurator.get_logger(cls._LOGGER_NAME)
        fields: dict[str, Any] = {
            "method": ctx.method,
            "route": ctx.route,
            "status_code": status,
            "duration_ms": duration_ms,
        }
        if error_class is not None:
            fields["error_class"] = error_class

        if status >= 500 or error_class is not None:
            logger.error("http_request", **fields)
        elif status >= 400:
            logger.warning("http_request", **fields)
        else:
            logger.info("http_request", **fields)


class RequestContextMiddleware:
    """Pure-ASGI middleware that owns request_id propagation and access logs.

    Pure ASGI (not Starlette ``BaseHTTPMiddleware``) because the runtime API
    serves SSE streams; the latter buffers responses and would break streaming.
    """

    _ID_PREFIX = "req_"

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
    ) -> None:
        self._app = app

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        headers = self._decode_headers(scope.get("headers") or [])
        request_id = headers.get(REQUEST_ID_HEADER) or self._new_request_id()
        org_id = headers.get(ORG_HEADER) or None
        user_id = headers.get(USER_HEADER) or None
        method = scope.get("method")

        ctx = HttpRequestContext(
            request_id=request_id,
            org_id=org_id,
            user_id=user_id,
            method=method,
            route=None,
        )
        token = HttpRequestContextHolder.set(ctx)
        started = time.perf_counter()
        status_holder = _StatusHolder()

        async def send_wrapper(message):  # type: ignore[no-untyped-def]
            if message.get("type") == "http.response.start":
                status_holder.code = int(message.get("status", 0))
                message = self._inject_request_id_header(message, request_id)
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        except Exception as exc:
            HttpAccessLogEmitter.emit(
                self._with_route(ctx, scope),
                status=500,
                duration_ms=self._duration_ms(started),
                error_class=type(exc).__name__,
            )
            raise
        else:
            HttpAccessLogEmitter.emit(
                self._with_route(ctx, scope),
                status=status_holder.code,
                duration_ms=self._duration_ms(started),
                error_class=None,
            )
        finally:
            HttpRequestContextHolder.reset(token)

    @classmethod
    def _new_request_id(cls) -> str:
        return f"{cls._ID_PREFIX}{uuid.uuid4().hex}"

    @staticmethod
    def _decode_headers(raw: list) -> dict[str, str]:
        decoded: dict[str, str] = {}
        for key, value in raw:
            try:
                k = key.decode("latin-1").lower()
                v = value.decode("latin-1")
            except (AttributeError, UnicodeDecodeError):
                continue
            decoded[k] = v
        return decoded

    @staticmethod
    def _inject_request_id_header(message, request_id: str):  # type: ignore[no-untyped-def]
        headers = list(message.get("headers") or [])
        headers.append(
            (REQUEST_ID_HEADER.encode("latin-1"), request_id.encode("latin-1"))
        )
        return {**message, "headers": headers}

    @staticmethod
    def _duration_ms(started: float) -> int:
        return max(0, int((time.perf_counter() - started) * 1000))

    @staticmethod
    def _with_route(ctx: HttpRequestContext, scope) -> HttpRequestContext:  # type: ignore[no-untyped-def]
        route = scope.get("route")
        path = getattr(route, "path", None) if route is not None else None
        resolved = (
            path if isinstance(path, str) and path else (scope.get("path") or None)
        )
        return ctx.model_copy(update={"route": resolved})


class _StatusHolder:
    __slots__ = ("code",)

    def __init__(self) -> None:
        self.code = 0
