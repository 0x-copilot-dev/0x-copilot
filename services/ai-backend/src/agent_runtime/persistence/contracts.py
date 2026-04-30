"""Typed persistence records for the durable agent runtime schema."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import Field, NonNegativeInt, PositiveInt, field_validator

from agent_runtime.agent.contracts import JsonObject, RuntimeContract, RuntimeErrorEnvelope
from agent_runtime.observability.redaction import ObservabilityRedactor
from agent_runtime.persistence.constants import Keys, Messages, Patterns


class OutboxStatus(StrEnum):
    """Durable runtime command lifecycle."""

    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"


class AsyncTaskStatus(StrEnum):
    """Persisted async task lifecycle outside message history."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class ToolInvocationStatus(StrEnum):
    """Persisted tool invocation lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ToolSideEffectClass(StrEnum):
    """Stable side-effect classes for audit and approval policy."""

    READ = "read"
    WRITE = "write"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    DESTRUCTIVE = "destructive"


class ApprovalRiskClass(StrEnum):
    """Risk classes shown to users before side effects execute."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PersistenceApprovalStatus(StrEnum):
    """Persisted approval request state."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RuntimeMemoryScopeType(StrEnum):
    """Persisted memory namespaces."""

    USER = "user"
    ORGANIZATION = "organization"
    ASSISTANT = "assistant"
    CONVERSATION = "conversation"


class PayloadKind(StrEnum):
    """Classes of large payloads stored by reference."""

    TOOL_RESULT = "tool_result"
    CONTEXT = "context"
    ARTIFACT = "artifact"
    CHECKPOINT = "checkpoint"


class PayloadStorageBackend(StrEnum):
    """Storage backends for offloaded payload blobs."""

    POSTGRES = "postgres"
    OBJECT_STORAGE = "object_storage"
    LOCAL_FILE = "local_file"


class PayloadRedactionState(StrEnum):
    """How payload content was prepared before storage."""

    REDACTED = "redacted"
    TRUNCATED = "truncated"
    OFFLOADED = "offloaded"


class AuditActorType(StrEnum):
    """Actors that can write runtime audit records."""

    USER = "user"
    RUNTIME = "runtime"
    WORKER = "worker"
    SYSTEM = "system"


class AuditOutcome(StrEnum):
    """Result class for audit records."""

    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class PersistenceValueNormalizer:
    """Normalize and redact values entering durable persistence records."""

    @classmethod
    def normalize_id(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name)
        if not Patterns.ID.fullmatch(normalized):
            msg = Messages.Validation.id_contains_unsupported_characters(field_name)
            raise ValueError(msg)
        return normalized

    @classmethod
    def normalize_optional_id(cls, value: object, field_name: str) -> str | None:
        if value is None:
            return None
        return cls.normalize_id(value, field_name)

    @classmethod
    def normalize_slug(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name).lower()
        if not Patterns.SLUG.fullmatch(normalized):
            msg = Messages.Validation.stable_slug(field_name)
            raise ValueError(msg)
        return normalized

    @classmethod
    def normalize_optional_text(cls, value: object, field_name: str) -> str | None:
        if value is None:
            return None
        return cls.normalize_nonempty_string(value, field_name)

    @classmethod
    def normalize_nonempty_string(cls, value: object, field_name: str) -> str:
        if not isinstance(value, str):
            msg = Messages.Validation.string_required(field_name)
            raise ValueError(msg)
        normalized = value.strip()
        if not normalized:
            msg = Messages.Validation.nonempty_string(field_name)
            raise ValueError(msg)
        return normalized

    @classmethod
    def normalize_sha256(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name).lower()
        if not Patterns.HASH.fullmatch(normalized):
            msg = Messages.Validation.sha256(field_name)
            raise ValueError(msg)
        return normalized

    @classmethod
    def redact_json_object(cls, value: object) -> JsonObject:
        return ObservabilityRedactor.redact_json_object(value)  # type: ignore[return-value]


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
    payload: JsonObject = Field(default_factory=dict)
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

    @field_validator(Keys.Field.PAYLOAD, mode="before")
    @classmethod
    def _redact_payload(cls, value: object) -> JsonObject:
        return PersistenceValueNormalizer.redact_json_object(value)


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


class AsyncTaskRecord(RuntimeContract):
    """Persisted async subagent task metadata."""

    task_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    conversation_id: str
    org_id: str
    parent_task_id: str | None = None
    subagent_name: str
    thread_id: str | None = None
    langgraph_run_id: str | None = None
    status: AsyncTaskStatus = AsyncTaskStatus.QUEUED
    objective_summary: str
    constraints: JsonObject = Field(default_factory=dict)
    output_contract: JsonObject = Field(default_factory=dict)
    timeout_seconds: PositiveInt | None = None
    started_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    safe_error_code: str | None = None
    safe_error_message: str | None = None


class SubagentResultRecord(RuntimeContract):
    """Persisted subagent result and compact summaries."""

    result_id: str = Field(default_factory=lambda: uuid4().hex)
    task_id: str
    run_id: str
    response_text: str | None = None
    execution_summary: str | None = None
    plan_summary: str | None = None
    artifacts: JsonObject = Field(default_factory=dict)
    recent_messages_ref: str | None = None
    error: JsonObject | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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


class PersistenceApprovalRequestRecord(RuntimeContract):
    """Persisted approval request for a side-effecting runtime action."""

    approval_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    tool_invocation_id: str | None = None
    org_id: str
    requested_by_user_id: str
    status: PersistenceApprovalStatus = PersistenceApprovalStatus.PENDING
    risk_class: ApprovalRiskClass = ApprovalRiskClass.MEDIUM
    action_summary: str
    request_payload: JsonObject = Field(default_factory=dict)
    decided_by_user_id: str | None = None
    decision_reason: str | None = None
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None

    @field_validator("request_payload", mode="before")
    @classmethod
    def _redact_request_payload(cls, value: object) -> JsonObject:
        return PersistenceValueNormalizer.redact_json_object(value)


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


class CompressionEventRecord(RuntimeContract):
    """Redacted context compression telemetry."""

    compression_event_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    org_id: str
    before_tokens: NonNegativeInt
    after_tokens: NonNegativeInt
    strategy: str
    payload_refs: JsonObject = Field(default_factory=dict)
    trace_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CapabilitySnapshotRecord(RuntimeContract):
    """Model-visible capability summary available during a run."""

    snapshot_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    org_id: str
    capability_type: str
    capability_name: str
    capability_version: str | None = None
    scopes: JsonObject = Field(default_factory=dict)
    risk_class: str | None = None
    summary: str
    loaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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


PERSISTENCE_TABLE_RECORDS = (
    OutboxEventRecord,
    ConsumerCursorRecord,
    AsyncTaskRecord,
    SubagentResultRecord,
    ToolInvocationRecord,
    PersistenceApprovalRequestRecord,
    MemoryScopeRecord,
    MemoryItemRecord,
    ContextPayloadRecord,
    CompressionEventRecord,
    CapabilitySnapshotRecord,
    AuditLogRecord,
    CheckpointRecord,
)
