"""Pydantic-validated structured log records for the backend facade.

The shape is fixed: free-form ``message`` text is intentionally absent. Callers
emit a short ``event`` label (e.g. ``proxy.upstream.error``) plus typed fields.
``metadata`` accepts only scalars and is redacted against a sensitive-key
denylist. This is the structural enforcement of the "no LLM I/O or PII in logs"
rule -- there is no string-search PII scrubbing, just refusal to accept
free-form payloads.
"""

from __future__ import annotations

from enum import StrEnum
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


_ALLOWED_METADATA_VALUE_TYPES = (str, int, float, bool, type(None))

_SENSITIVE_KEY = re.compile(
    r"(api[_-]?key|authorization|credential|password|secret|token|cookie|session)",
    re.I,
)


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class LogEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str = Field(min_length=1)
    env: str = Field(min_length=1)
    level: LogLevel = LogLevel.INFO
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
        return self.model_dump(mode="json", exclude_none=True)


class MetadataRedactor:
    @classmethod
    def redact(cls, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            if _SENSITIVE_KEY.search(key):
                continue
            if not isinstance(item, _ALLOWED_METADATA_VALUE_TYPES):
                continue
            result[key] = item
        return result
