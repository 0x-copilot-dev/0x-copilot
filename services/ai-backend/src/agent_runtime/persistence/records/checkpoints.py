"""Runtime checkpoint metadata records."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field, PositiveInt, field_validator

from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.persistence.constants import Keys
from agent_runtime.persistence.records.common import PersistenceValueNormalizer


class CheckpointRecord(RuntimeContract):
    """LangGraph/runtime checkpoint metadata and blob reference."""

    checkpoint_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    thread_id: str
    checkpoint_namespace: str
    checkpoint_version: PositiveInt
    checkpoint_blob_ref: str
    metadata: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(Keys.Field.METADATA, mode="before")
    @classmethod
    def _redact_metadata(cls, value: object) -> JsonObject:
        return PersistenceValueNormalizer.redact_json_object(value)
