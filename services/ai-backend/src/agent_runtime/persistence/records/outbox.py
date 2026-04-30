"""Outbox, worker claim, and consumer cursor records."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field, NonNegativeInt, PositiveInt, field_validator

from agent_runtime.execution.contracts import JsonObject, RuntimeContract, RuntimeErrorEnvelope
from agent_runtime.persistence.constants import Keys
from agent_runtime.persistence.records.common import OutboxStatus, PersistenceValueNormalizer


class OutboxEventRecord(RuntimeContract):
    """Durable command or integration event used by runtime consumers."""

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    aggregate_type: str
    aggregate_id: str
    org_id: str
    event_type: str
    payload: JsonObject = Field(default_factory=dict)
    status: OutboxStatus = OutboxStatus.PENDING
    attempts: NonNegativeInt = 0
    available_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    locked_by: str | None = None
    lock_expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(Keys.Field.EVENT_ID, Keys.Field.AGGREGATE_ID, Keys.Field.ORG_ID)
    @classmethod
    def _normalize_ids(cls, value: object, info) -> str:
        return PersistenceValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.AGGREGATE_TYPE, Keys.Field.EVENT_TYPE)
    @classmethod
    def _normalize_slugs(cls, value: object, info) -> str:
        return PersistenceValueNormalizer.normalize_slug(value, info.field_name)

    @field_validator(Keys.Field.LOCKED_BY, mode="before")
    @classmethod
    def _normalize_optional_locked_by(cls, value: object) -> str | None:
        return PersistenceValueNormalizer.normalize_optional_id(value, Keys.Field.LOCKED_BY)

    @field_validator(Keys.Field.PAYLOAD, mode="before")
    @classmethod
    def _redact_payload(cls, value: object) -> JsonObject:
        return PersistenceValueNormalizer.redact_json_object(value)



class RuntimeWorkerClaim(RuntimeContract):
    """A worker-owned claim on a durable runtime command."""

    claim_id: str = Field(default_factory=lambda: uuid4().hex)
    command_id: str
    command_type: str
    org_id: str
    run_id: str | None = None
    approval_id: str | None = None
    locked_by: str
    lock_expires_at: datetime
    attempts: PositiveInt = 1
    payload: dict[str, object] = Field(default_factory=dict)
    claimed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(
        "claim_id",
        Keys.Field.COMMAND_ID,
        Keys.Field.ORG_ID,
        Keys.Field.LOCKED_BY,
        mode="before",
    )
    @classmethod
    def _normalize_ids(cls, value: object, info) -> str:
        return PersistenceValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.RUN_ID, Keys.Field.APPROVAL_ID, mode="before")
    @classmethod
    def _normalize_optional_ids(cls, value: object, info) -> str | None:
        return PersistenceValueNormalizer.normalize_optional_id(value, info.field_name)

    @field_validator("command_type")
    @classmethod
    def _normalize_command_type(cls, value: object) -> str:
        return PersistenceValueNormalizer.normalize_slug(value, "command_type")

class RuntimeWorkerResult(RuntimeContract):
    """A worker result used to complete, retry, or dead-letter a claim."""

    command_id: str
    succeeded: bool
    safe_error: RuntimeErrorEnvelope | None = None
    retry_available_at: datetime | None = None
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(Keys.Field.COMMAND_ID)
    @classmethod
    def _normalize_command_id(cls, value: object) -> str:
        return PersistenceValueNormalizer.normalize_id(value, Keys.Field.COMMAND_ID)



class ConsumerCursorRecord(RuntimeContract):
    """Durable replay cursor for consumers that need their own position."""

    consumer_name: str
    run_id: str
    last_sequence_no: NonNegativeInt = 0
    last_event_id: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(Keys.Field.RUN_ID)
    @classmethod
    def _normalize_run_id(cls, value: object) -> str:
        return PersistenceValueNormalizer.normalize_id(value, Keys.Field.RUN_ID)

    @field_validator("last_event_id", mode="before")
    @classmethod
    def _normalize_optional_event_id(cls, value: object) -> str | None:
        return PersistenceValueNormalizer.normalize_optional_id(value, "last_event_id")

    @field_validator(Keys.Field.CONSUMER_NAME)
    @classmethod
    def _normalize_consumer_name(cls, value: object) -> str:
        return PersistenceValueNormalizer.normalize_slug(value, Keys.Field.CONSUMER_NAME)
