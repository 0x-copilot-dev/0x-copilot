"""PII-safe structured logging for runtime operations."""

from __future__ import annotations

from enum import StrEnum
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from agent_runtime.observability.constants import Patterns
from agent_runtime.validation import ValueNormalizer

_logger = logging.getLogger("agent_runtime")

_ALLOWED_METADATA_VALUE_TYPES = (str, int, float, bool)


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
        return _MetadataRedactor.redact(value)

    def to_log_dict(self) -> dict[str, object]:
        """Return a JSON-serializable record without empty optional fields."""

        return self.model_dump(mode="json", exclude_none=True)


class _MetadataRedactor:
    """Denylist-based redaction for structured logging metadata."""

    @classmethod
    def redact(cls, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, object] = {}
        dropped: list[str] = []
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            if Patterns.SENSITIVE_KEY.search(key):
                dropped.append(key)
                continue
            if item is not None and not isinstance(item, _ALLOWED_METADATA_VALUE_TYPES):
                dropped.append(key)
                continue
            result[key] = item
        if dropped:
            _logger.debug("Dropped metadata keys from log event: %s", dropped)
        return result


class RuntimeLogger:
    """Adapter that emits structured runtime log records through stdlib logging."""

    LEVELS = {
        RuntimeLogLevel.DEBUG: logging.DEBUG,
        RuntimeLogLevel.INFO: logging.INFO,
        RuntimeLogLevel.WARNING: logging.WARNING,
        RuntimeLogLevel.ERROR: logging.ERROR,
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
