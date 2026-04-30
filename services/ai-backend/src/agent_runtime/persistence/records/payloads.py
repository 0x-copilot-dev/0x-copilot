"""Large context payload reference records."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field, NonNegativeInt, field_validator

from agent_runtime.agent.contracts import RuntimeContract
from agent_runtime.persistence.constants import Keys
from agent_runtime.persistence.records.common import PayloadKind, PayloadRedactionState, PayloadStorageBackend, PersistenceValueNormalizer


class ContextPayloadRecord(RuntimeContract):
    """Reference to a large payload stored outside primary runtime rows."""

    payload_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    task_id: str | None = None
    tool_invocation_id: str | None = None
    org_id: str
    kind: PayloadKind
    storage_backend: PayloadStorageBackend
    storage_uri: str
    sha256: str
    byte_size: NonNegativeInt
    mime_type: str | None = None
    redaction_state: PayloadRedactionState = PayloadRedactionState.OFFLOADED
    retention_until: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(Keys.Field.SHA256)
    @classmethod
    def _normalize_sha256(cls, value: object) -> str:
        return PersistenceValueNormalizer.normalize_sha256(value, Keys.Field.SHA256)
