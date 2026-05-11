"""PII-safe structured logging for runtime operations."""

from __future__ import annotations

from enum import StrEnum
import logging
import traceback
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from agent_runtime.observability.redactor import MetadataRedactor, SafeLogDumper
from agent_runtime.validation import ValueNormalizer


class _Fields:
    """Canonical field-name constants for log record validators."""

    EVENT = "event"
    SUBSYSTEM = "subsystem"
    OPERATION = "operation"
    STATUS = "status"
    REQUEST_ID = "request_id"
    RUN_ID = "run_id"
    TRACE_ID = "trace_id"
    PARENT_TRACE_ID = "parent_trace_id"
    ERROR_CODE = "error_code"
    SAFE_MESSAGE = "safe_message"
    METADATA = "metadata"


class RuntimeLogLevel(StrEnum):
    """Supported structured log levels."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class RuntimeLogEvent(BaseModel):
    """Structured runtime log record with product and sub-trace IDs."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    event: str = Field(min_length=1)
    level: RuntimeLogLevel = RuntimeLogLevel.INFO
    request_id: str
    run_id: str
    trace_id: str
    parent_trace_id: str | None = None
    subsystem: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    status: str = Field(min_length=1)
    duration_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    retryable: bool | None = None
    safe_message: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator(
        _Fields.EVENT,
        _Fields.SUBSYSTEM,
        _Fields.OPERATION,
        _Fields.STATUS,
        mode="before",
    )
    @classmethod
    def _strip_label(cls, value: object, info: ValidationInfo) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{info.field_name} must be a string")
        return value.strip()

    @field_validator(_Fields.REQUEST_ID, _Fields.RUN_ID, _Fields.TRACE_ID)
    @classmethod
    def _normalize_required_id(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(_Fields.PARENT_TRACE_ID)
    @classmethod
    def _normalize_optional_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return ValueNormalizer.normalize_id(value, _Fields.PARENT_TRACE_ID)

    @field_validator(_Fields.ERROR_CODE, _Fields.SAFE_MESSAGE)
    @classmethod
    def _normalize_optional_text(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str | None:
        if value is None:
            return None
        return ValueNormalizer.normalize_nonempty_string(value, info.field_name)

    @field_validator(_Fields.METADATA, mode="before")
    @classmethod
    def _redact_metadata(cls, value: object) -> dict[str, object]:
        return MetadataRedactor.redact(value)

    def to_log_dict(self) -> dict[str, object]:
        """Return a JSON-serializable record without empty optional fields.

        Routes through :class:`SafeLogDumper` so any field annotated
        ``Sensitive(...)`` is elided. The current ``RuntimeLogEvent``
        fields are all log-safe (structural names, IDs, status, safe
        messages) so this is a no-op today — the integration is in
        place for future taggings via P11.3.
        """

        return SafeLogDumper.dump_safe(self, mode="json", exclude_none=True)


class RuntimeLogger:
    """Adapter that emits structured runtime log records through stdlib logging."""

    LEVELS = {
        RuntimeLogLevel.DEBUG: logging.DEBUG,
        RuntimeLogLevel.INFO: logging.INFO,
        RuntimeLogLevel.WARNING: logging.WARNING,
        RuntimeLogLevel.ERROR: logging.ERROR,
    }

    class ExceptionMetadata:
        """Canonical keys for the exception-metadata helper."""

        EXCEPTION_TYPE = "exception_type"
        EXCEPTION_MESSAGE = "exception_message"
        TRACEBACK = "traceback"
        MESSAGE_MAX_CHARS_DEFAULT = 1000
        FRAMES_DEFAULT = 8

    @classmethod
    def exception_metadata(
        cls,
        exc: BaseException,
        *,
        message_max_chars: int | None = None,
        frames: int | None = None,
    ) -> dict[str, str]:
        """Single source of truth for "how we log an exception" in server logs.

        Returns the canonical ``{exception_type, exception_message, traceback}``
        triple as ``RuntimeLogEvent.metadata``-shaped primitives. Callers
        should always use this helper instead of building exception metadata
        ad-hoc, so the message-length truncation and traceback shape are
        uniform across the codebase.

        The output is server-side metadata only. It is bound into
        :class:`RuntimeLogEvent.metadata`, which goes through
        :class:`MetadataRedactor` at log emission — that step drops any
        deny-keyed entries (it does not value-scan the message text). If an
        upstream caller bakes a credential into an exception's ``str(exc)``,
        the credential reaches the log. **Don't bake credentials into
        exception messages.** P11.5 (parent PRD §8) made this a tool-emission
        hygiene concern rather than a runtime scrub-on-log behavior.
        """

        max_chars = (
            message_max_chars
            if message_max_chars is not None
            else cls.ExceptionMetadata.MESSAGE_MAX_CHARS_DEFAULT
        )
        max_frames = (
            frames if frames is not None else cls.ExceptionMetadata.FRAMES_DEFAULT
        )

        message = str(exc) if str(exc) else repr(exc)
        if len(message) > max_chars:
            message = message[: max_chars - 1] + "…"

        tb = traceback.extract_tb(exc.__traceback__)
        frame_strings = [
            f"{frame.filename}:{frame.lineno}" for frame in tb[-max_frames:]
        ]
        traceback_summary = " -> ".join(frame_strings)

        return {
            cls.ExceptionMetadata.EXCEPTION_TYPE: type(exc).__name__,
            cls.ExceptionMetadata.EXCEPTION_MESSAGE: message,
            cls.ExceptionMetadata.TRACEBACK: traceback_summary,
        }

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("agent_runtime")

    def emit(self, event: RuntimeLogEvent) -> RuntimeLogEvent:
        payload = event.to_log_dict()
        self.logger.log(
            self.LEVELS[event.level],
            event.event,
            extra={"runtime": payload},
        )
        return event

    def event(
        self,
        *,
        context: object,
        event: str,
        level: RuntimeLogLevel = RuntimeLogLevel.INFO,
        subsystem: str,
        operation: str,
        status: str,
        duration_ms: int | None = None,
        error_code: str | None = None,
        retryable: bool | None = None,
        safe_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeLogEvent:
        return self.emit(
            RuntimeLogEvent(
                event=event,
                level=level,
                request_id=str(getattr(context, "request_id")),
                run_id=str(getattr(context, "run_id")),
                trace_id=str(getattr(context, "trace_id")),
                parent_trace_id=getattr(context, "parent_trace_id", None),
                subsystem=subsystem,
                operation=operation,
                status=status,
                duration_ms=duration_ms,
                error_code=error_code,
                retryable=retryable,
                safe_message=safe_message,
                metadata=metadata or {},
            )
        )
