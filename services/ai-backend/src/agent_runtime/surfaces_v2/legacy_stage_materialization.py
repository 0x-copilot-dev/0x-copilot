"""Durable fence facts for E2 legacy-stage materialization.

The E2 migration may only create a canonical *held* effect stage after the
old staged-write projection is proven unchanged.  A reservation alone is not
enough: a process can crash or another writer can mutate the old projection
between reserving it and appending ``effect.staged``.  The event-store fence
defined here is therefore consumed by the append operation itself.

Only opaque identifiers and digests cross this boundary.  Proposal bytes,
arguments, approvals, and executable commands never appear here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import Field, field_validator

from agent_runtime.execution.contracts import RuntimeContract


# Kept deliberately low-entropy: this is an event-metadata namespace, never a secret.
MATERIALIZATION_FENCE_METADATA_KEY = "migration-fence"


class LegacyStageMaterializationState(StrEnum):
    """Durable progress for one legacy source and deterministic stage id."""

    RESERVED = "reserved"
    STAGED = "staged"
    MAPPED = "mapped"
    QUARANTINED = "quarantined"
    RELEASED = "released"


class LegacyStageReconciliationState(StrEnum):
    """Durable, worker-inert state for claimed/indeterminate legacy work."""

    FROZEN = "frozen"
    RELEASED = "released"
    QUARANTINED = "quarantined"


class LegacyStageMaterializationFence(RuntimeContract):
    """The append-time compare-and-set precondition for one canonical stage."""

    org_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    legacy_stage_id: str = Field(min_length=1, max_length=256)
    source_digest: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=256)
    canonical_stage_id: str = Field(min_length=1, max_length=256)

    @field_validator(
        "org_id", "run_id", "legacy_stage_id", "idempotency_key", "canonical_stage_id"
    )
    @classmethod
    def _opaque(cls, value: str) -> str:
        if not value or any(char.isspace() for char in value):
            raise ValueError("materialization identifier is invalid")
        return value

    @field_validator("source_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("materialization digest is invalid")
        return value


class LegacyStageMaterializationRecord(RuntimeContract):
    """Crash-recoverable reservation state, never an executable work item."""

    org_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    legacy_stage_id: str = Field(min_length=1, max_length=256)
    source_digest: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=256)
    canonical_stage_id: str = Field(min_length=1, max_length=256)
    state: LegacyStageMaterializationState
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("materialization timestamp requires timezone")
        return value.astimezone(timezone.utc)

    def fence(self) -> LegacyStageMaterializationFence:
        """Return the exact append-time guard for this durable record."""

        return LegacyStageMaterializationFence(
            org_id=self.org_id,
            run_id=self.run_id,
            legacy_stage_id=self.legacy_stage_id,
            source_digest=self.source_digest,
            idempotency_key=self.idempotency_key,
            canonical_stage_id=self.canonical_stage_id,
        )


class LegacyStageMaterializationRejected(RuntimeError):
    """The append-time fence refused stale, forged, or released source facts."""


class LegacyStageReconciliationRecord(RuntimeContract):
    """Checkpointed observation-only task; it has no command or executor field."""

    org_id: str
    run_id: str
    legacy_stage_id: str
    source_digest: str
    state: LegacyStageReconciliationState
    checkpoint_revision: int = Field(ge=0)
    operator_ref: str
    migration_job_id: str
    reassessed_at: datetime
    terminal_at: datetime | None = None


def materialization_fence_from_metadata(
    metadata: object,
) -> LegacyStageMaterializationFence | None:
    """Parse a private append guard without treating arbitrary metadata as one."""

    if not isinstance(metadata, dict):
        return None
    raw = metadata.get(MATERIALIZATION_FENCE_METADATA_KEY)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise LegacyStageMaterializationRejected(
            "legacy materialization fence is invalid"
        )
    try:
        return LegacyStageMaterializationFence.model_validate(raw)
    except (TypeError, ValueError) as exc:
        raise LegacyStageMaterializationRejected(
            "legacy materialization fence is invalid"
        ) from exc


__all__ = [
    "LegacyStageMaterializationFence",
    "LegacyStageMaterializationRecord",
    "LegacyStageMaterializationRejected",
    "LegacyStageMaterializationState",
    "LegacyStageReconciliationRecord",
    "LegacyStageReconciliationState",
    "MATERIALIZATION_FENCE_METADATA_KEY",
    "materialization_fence_from_metadata",
]
