"""Persisted tool invocation records."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from pydantic import Field, field_validator

from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.persistence.constants import Keys
from agent_runtime.persistence.records.common import PersistenceValueNormalizer, ToolInvocationStatus, ToolSideEffectClass


class ToolInvocationRecord(RuntimeContract):
    """Persisted tool invocation state with redacted inputs and outputs."""

    invocation_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    task_id: str | None = None
    org_id: str
    tool_name: str
    connector_slug: str | None = None
    side_effect_class: ToolSideEffectClass = ToolSideEffectClass.READ
    call_id: str | None = None
    status: ToolInvocationStatus = ToolInvocationStatus.QUEUED
    args: JsonObject = Field(default_factory=dict)
    result_summary: JsonObject = Field(default_factory=dict)
    approval_id: str | None = None
    external_ref: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    safe_error_code: str | None = None
    safe_error_message: str | None = None

    @field_validator(Keys.Field.ORG_ID, Keys.Field.RUN_ID, mode="before")
    @classmethod
    def _normalize_ids(cls, value: object, info) -> str:
        return PersistenceValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.TOOL_NAME)
    @classmethod
    def _normalize_tool_name(cls, value: object) -> str:
        return PersistenceValueNormalizer.normalize_slug(value, Keys.Field.TOOL_NAME)

    @field_validator("connector_slug", mode="before")
    @classmethod
    def _normalize_optional_connector_slug(cls, value: object) -> str | None:
        if value is None:
            return None
        return PersistenceValueNormalizer.normalize_slug(value, "connector_slug")

    @field_validator("args", "result_summary", mode="before")
    @classmethod
    def _redact_json_fields(cls, value: object) -> JsonObject:
        return PersistenceValueNormalizer.redact_json_object(value)
