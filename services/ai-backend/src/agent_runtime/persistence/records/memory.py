"""Persisted runtime memory metadata records."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field, PositiveInt, field_validator

from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.persistence.constants import Keys
from agent_runtime.persistence.records.common import PersistenceValueNormalizer, RuntimeMemoryScopeType


class MemoryScopeRecord(RuntimeContract):
    """Persisted memory namespace metadata."""

    scope_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    user_id: str | None = None
    assistant_id: str | None = None
    scope_type: RuntimeMemoryScopeType
    namespace_hash: str
    namespace: JsonObject = Field(default_factory=dict)
    policy_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(Keys.Field.NAMESPACE_HASH)
    @classmethod
    def _normalize_namespace_hash(cls, value: object) -> str:
        return PersistenceValueNormalizer.normalize_sha256(value, Keys.Field.NAMESPACE_HASH)

    @field_validator("namespace", mode="before")
    @classmethod
    def _redact_namespace(cls, value: object) -> JsonObject:
        return PersistenceValueNormalizer.redact_json_object(value)



class MemoryItemRecord(RuntimeContract):
    """Persisted memory item metadata and content reference."""

    item_id: str = Field(default_factory=lambda: uuid4().hex)
    scope_id: str
    org_id: str
    path: str
    content_ref: str
    content_summary: str | None = None
    checksum: str
    version: PositiveInt = 1
    created_by_run_id: str | None = None
    updated_by_run_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deleted_at: datetime | None = None

    @field_validator(Keys.Field.CHECKSUM)
    @classmethod
    def _normalize_checksum(cls, value: object) -> str:
        return PersistenceValueNormalizer.normalize_sha256(value, Keys.Field.CHECKSUM)
