"""Shared persistence enums and value normalization."""

from __future__ import annotations

from enum import StrEnum


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
    """Persisted approval request state.

    PR 1.4 — ``FORWARDED`` is a terminal state for the *parent* row in a
    two-stage approval chain. The runtime worker never resumes the
    LangGraph interrupt on ``FORWARDED``; resume happens on the leaf
    child's ``APPROVED`` / ``REJECTED`` instead.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    FORWARDED = "forwarded"


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
    """Normalize and redact values entering durable persistence records.

    All common methods delegate to the shared ``ValueNormalizer``.
    """

    from agent_runtime.validation import ValueNormalizer as _V
    from agent_runtime.observability.redactor import (
        JsonObjectCoercer as _Coercer,
    )

    normalize_nonempty_string = _V.normalize_nonempty_string
    normalize_id = _V.normalize_id
    normalize_optional_id = _V.normalize_optional_id
    normalize_slug = _V.normalize_slug
    normalize_optional_text = _V.normalize_optional_text
    normalize_sha256 = _V.normalize_sha256
    coerce_json_object = _Coercer.coerce

    del _V, _Coercer
