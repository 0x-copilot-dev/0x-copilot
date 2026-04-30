"""Append-only runtime audit records."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field, field_validator

from agent_runtime.agent.contracts import JsonObject, RuntimeContract
from agent_runtime.persistence.constants import Keys
from agent_runtime.persistence.records.common import AuditActorType, AuditOutcome, PersistenceValueNormalizer


class AuditLogRecord(RuntimeContract):
    """Append-only security and operational audit event."""

    audit_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    user_id: str | None = None
    actor_type: AuditActorType
    action: str
    resource_type: str
    resource_id: str
    run_id: str | None = None
    trace_id: str | None = None
    outcome: AuditOutcome
    metadata: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(Keys.Field.METADATA, mode="before")
    @classmethod
    def _redact_metadata(cls, value: object) -> JsonObject:
        return PersistenceValueNormalizer.redact_json_object(value)
