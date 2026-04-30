"""PII-safe structured logging for runtime operations."""

from __future__ import annotations

from enum import StrEnum
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

_ID_EXTRA_CHARS = frozenset({".", "_", ":", "-"})
_ALLOWED_METADATA_KEYS = frozenset(
    {
        "after_tokens",
        "attempt",
        "before_tokens",
        "duration_ms",
        "exception_type",
        "fallback_used",
        "message_count",
        "resource_count",
        "retry_count",
        "safe_count",
        "schema_name",
        "token_count",
        "tool_count",
    }
)
_ALLOWED_METADATA_VALUE_TYPES = (str, int, float, bool)


class RuntimeLogLevel(StrEnum):
    """Supported structured log levels."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class RuntimeLogEvent(BaseModel):
    """Structured runtime log record with product and sub-trace IDs."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    event: str
    level: RuntimeLogLevel = RuntimeLogLevel.INFO
    request_id: str
    run_id: str
    trace_id: str
    parent_trace_id: str | None = None
    subsystem: str
    operation: str
    status: str
    duration_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    retryable: bool | None = None
    safe_message: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("event", "subsystem", "operation", "status")
    @classmethod
    def _normalize_label(cls, value: object, info: ValidationInfo) -> str:
        if not isinstance(value, str):
            msg = f"{info.field_name} must be a string"
            raise ValueError(msg)
        normalized = value.strip()
        if not normalized:
            msg = f"{info.field_name} must not be empty"
            raise ValueError(msg)
        return normalized

    @field_validator("request_id", "run_id", "trace_id")
    @classmethod
    def _normalize_required_id(cls, value: object, info: ValidationInfo) -> str:
        return LogValueNormalizer.normalize_id(value, info.field_name)

    @field_validator("parent_trace_id")
    @classmethod
    def _normalize_optional_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return LogValueNormalizer.normalize_id(value, "parent_trace_id")

    @field_validator("error_code", "safe_message")
    @classmethod
    def _normalize_optional_text(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            msg = f"{info.field_name} must be a string"
            raise ValueError(msg)
        normalized = value.strip()
        if not normalized:
            msg = f"{info.field_name} must not be empty"
            raise ValueError(msg)
        return normalized

    @field_validator("metadata", mode="before")
    @classmethod
    def _redact_metadata(cls, value: object) -> dict[str, object]:
        return LogValueNormalizer.redact_metadata(value)

    def to_log_dict(self) -> dict[str, object]:
        """Return a JSON-serializable record without empty optional fields."""

        return self.model_dump(mode="json", exclude_none=True)


class LogValueNormalizer:
    """Allowlist helpers for structured logging metadata."""

    @classmethod
    def normalize_id(cls, value: object, field_name: str) -> str:
        if not isinstance(value, str):
            msg = f"{field_name} must be a string"
            raise ValueError(msg)
        normalized = value.strip()
        if not normalized or not cls.is_safe_id(normalized):
            msg = f"{field_name} contains unsupported characters"
            raise ValueError(msg)
        return normalized

    @classmethod
    def is_safe_id(cls, value: str) -> bool:
        return value[0].isalnum() and all(
            character.isalnum() or character in _ID_EXTRA_CHARS for character in value
        )

    @classmethod
    def redact_metadata(cls, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        return {
            key: item
            for key, item in value.items()
            if cls.is_allowed_metadata_item(key, item)
        }

    @classmethod
    def is_allowed_metadata_item(cls, key: object, value: object) -> bool:
        return (
            isinstance(key, str)
            and key in _ALLOWED_METADATA_KEYS
            and (value is None or isinstance(value, _ALLOWED_METADATA_VALUE_TYPES))
        )


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
