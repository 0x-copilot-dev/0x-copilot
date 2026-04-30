"""Shared persistence enums and value normalization."""

from __future__ import annotations

from enum import StrEnum

from agent_runtime.execution.contracts import JsonObject
from agent_runtime.observability.redaction import ObservabilityRedactor
from agent_runtime.persistence.constants import Messages, Patterns


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
