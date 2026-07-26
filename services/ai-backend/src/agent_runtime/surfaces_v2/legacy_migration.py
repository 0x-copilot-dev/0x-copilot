"""Fail-closed contracts for the E2 legacy draft/staged-write migration.

This module deliberately contains only deterministic, redacted migration
facts and a tiny checkpoint port.  It has no HTTP, adapter, filesystem,
queue, or executor dependency.  In particular it cannot turn a legacy staged
write into an executable effect: callers may inventory and classify those
records, but must retain a fresh approval boundary before any later cutover.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import Protocol, runtime_checkable

from pydantic import Field, field_validator, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = 1


class LegacyMigrationStateError(RuntimeError):
    """A durable migration state cannot be trusted or updated safely."""

    def __init__(self) -> None:
        super().__init__("legacy migration state is unavailable")


class LegacyMigrationStatus(StrEnum):
    """The only durable states for one digest-bound migration run."""

    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    AUDIT_PENDING = "audit_pending"


class LegacyDraftDisposition(StrEnum):
    """Evidence result for one complete legacy draft history."""

    PENDING = "pending"
    VERIFIED = "verified"
    QUARANTINED = "quarantined"


class LegacyStageDisposition(StrEnum):
    """Safe E2 disposition for an old ``write.staged`` projection."""

    COMPATIBILITY_ONLY = "compatibility_only"
    REQUIRE_FRESH_APPROVAL = "require_fresh_approval"
    QUARANTINED = "quarantined"


class LegacyStageMigrationOutcome(StrEnum):
    """Durable, non-executing outcome for one pending legacy stage.

    This is intentionally more specific than :class:`LegacyStageDisposition`:
    the inventory disposition says what an old projection *looks like*, while
    this outcome records what the migration safely did.  In particular no
    outcome means that an old approval was copied to a new stage.
    """

    COMPATIBILITY_ONLY = "compatibility_only"
    CANONICAL_HELD = "canonical_held"
    FROZEN_RECONCILE = "frozen_reconcile"
    QUARANTINED = "quarantined"


def _opaque(value: str) -> str:
    if _OPAQUE.fullmatch(value) is None:
        raise ValueError("identifier must be a safe opaque token")
    return value


def _digest(value: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise ValueError("digest must be lowercase sha256 hex")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


class LegacyDraftVersionEvidence(RuntimeContract):
    """Redacted immutable evidence for one historic draft version."""

    version: int = Field(ge=1)
    content_digest: str = Field(min_length=64, max_length=64)
    title_digest: str = Field(min_length=64, max_length=64)
    byte_size: int = Field(ge=0)
    created_at: datetime

    @field_validator("content_digest", "title_digest")
    @classmethod
    def _content_digest(cls, value: str) -> str:
        return _digest(value)

    @field_validator("created_at")
    @classmethod
    def _created_at(cls, value: datetime) -> datetime:
        return _utc(value)


class LegacyDraftEvidence(RuntimeContract):
    """One tenant-scoped legacy draft history without its body or metadata."""

    draft_id: str = Field(min_length=1, max_length=256)
    scope_digest: str = Field(min_length=64, max_length=64)
    versions: tuple[LegacyDraftVersionEvidence, ...] = Field(min_length=1)
    source_digest: str = Field(min_length=64, max_length=64)
    eligible: bool
    validation_code: str = Field(min_length=1, max_length=80)

    @field_validator("draft_id")
    @classmethod
    def _draft_id(cls, value: str) -> str:
        return _opaque(value)

    @field_validator("scope_digest", "source_digest")
    @classmethod
    def _draft_digest(cls, value: str) -> str:
        return _digest(value)


class LegacyStageEvidence(RuntimeContract):
    """Redacted evidence for one old staged-write state folded from a run."""

    run_id: str = Field(min_length=1, max_length=256)
    stage_id: str = Field(min_length=1, max_length=256)
    source_digest: str = Field(min_length=64, max_length=64)
    disposition: LegacyStageDisposition
    status: str = Field(min_length=1, max_length=80)

    @field_validator("run_id", "stage_id")
    @classmethod
    def _stage_identifier(cls, value: str) -> str:
        return _opaque(value)

    @field_validator("source_digest")
    @classmethod
    def _stage_digest(cls, value: str) -> str:
        return _digest(value)


class LegacyStageMigrationRecord(RuntimeContract):
    """A source-digest-fenced checkpoint for exactly one legacy stage.

    The record stores no body, arguments, approval identity, queue payload, or
    executable command.  ``canonical_stage_id`` is present only for a newly
    created **unapproved** canonical stage.  A retry can therefore prove it is
    handling the exact same source without producing a duplicate stage.
    """

    org_id: str = Field(min_length=1, max_length=256)
    migration_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    legacy_stage_id: str = Field(min_length=1, max_length=256)
    source_digest: str = Field(min_length=64, max_length=64)
    outcome: LegacyStageMigrationOutcome
    canonical_stage_id: str | None = Field(default=None, min_length=1, max_length=256)
    queue_cancelled: bool = False
    reconciler_frozen: bool = False
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @field_validator(
        "org_id", "migration_id", "run_id", "legacy_stage_id", "canonical_stage_id"
    )
    @classmethod
    def _stage_migration_identifier(cls, value: str | None) -> str | None:
        return None if value is None else _opaque(value)

    @field_validator("source_digest")
    @classmethod
    def _stage_migration_digest(cls, value: str) -> str:
        return _digest(value)

    @field_validator("created_at", "updated_at")
    @classmethod
    def _stage_migration_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def _stage_migration_facts_are_consistent(self) -> LegacyStageMigrationRecord:
        if self.outcome is LegacyStageMigrationOutcome.CANONICAL_HELD:
            if self.canonical_stage_id is None or self.reconciler_frozen:
                raise ValueError("canonical held migration facts are inconsistent")
        elif self.canonical_stage_id is not None:
            raise ValueError("only canonical held records may name a canonical stage")
        if self.outcome is LegacyStageMigrationOutcome.FROZEN_RECONCILE:
            if not self.reconciler_frozen:
                raise ValueError("frozen reconcile records require a frozen reconciler")
        elif self.reconciler_frozen:
            raise ValueError("only frozen reconcile records may freeze a reconciler")
        return self


class LegacyMigrationInventory(RuntimeContract):
    """Complete bounded source inventory used as an apply-run fence."""

    org_id: str = Field(min_length=1, max_length=256)
    source_digest: str = Field(min_length=64, max_length=64)
    source_complete: bool
    drafts: tuple[LegacyDraftEvidence, ...] = ()
    stages: tuple[LegacyStageEvidence, ...] = ()

    @field_validator("org_id")
    @classmethod
    def _org_id(cls, value: str) -> str:
        return _opaque(value)

    @field_validator("source_digest")
    @classmethod
    def _source_digest(cls, value: str) -> str:
        return _digest(value)


class LegacyMigrationCheckpoint(RuntimeContract):
    """A CAS-protected, source-bound progress cursor.

    ``after_draft_id`` advances only after that entire history has been
    verified in the canonical artifact store or deliberately quarantined.  A
    crash may replay the current group, which is safe because artifact writes
    use deterministic identifiers and idempotency keys.
    """

    migration_id: str = Field(min_length=1, max_length=256)
    org_id: str = Field(min_length=1, max_length=256)
    source_digest: str = Field(min_length=64, max_length=64)
    after_draft_id: str | None = Field(default=None, min_length=1, max_length=256)
    status: LegacyMigrationStatus = LegacyMigrationStatus.RUNNING
    report_digest: str | None = Field(default=None, min_length=64, max_length=64)
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @field_validator("migration_id", "org_id", "after_draft_id")
    @classmethod
    def _checkpoint_identifier(cls, value: str | None) -> str | None:
        return None if value is None else _opaque(value)

    @field_validator("source_digest", "report_digest")
    @classmethod
    def _checkpoint_digest(cls, value: str | None) -> str | None:
        return None if value is None else _digest(value)

    @field_validator("created_at", "updated_at")
    @classmethod
    def _checkpoint_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class LegacyMigrationReport(RuntimeContract):
    """Deterministic, content-free cohort-enablement evidence."""

    migration_id: str = Field(min_length=1, max_length=256)
    org_id: str = Field(min_length=1, max_length=256)
    dry_run: bool
    source_digest: str = Field(min_length=64, max_length=64)
    migration_status: str = Field(min_length=1, max_length=80)
    drafts_total: int = Field(ge=0)
    drafts_verified: int = Field(ge=0)
    drafts_pending: int = Field(ge=0)
    drafts_quarantined: int = Field(ge=0)
    stages_compatibility_only: int = Field(ge=0)
    stages_requiring_fresh_approval: int = Field(ge=0)
    stages_quarantined: int = Field(ge=0)
    source_complete: bool
    audit_recorded: bool
    cohort_ready: bool
    blockers: tuple[str, ...] = ()
    report_digest: str = Field(min_length=64, max_length=64)

    @field_validator("migration_id", "org_id")
    @classmethod
    def _report_identifier(cls, value: str) -> str:
        return _opaque(value)

    @field_validator("source_digest", "report_digest")
    @classmethod
    def _report_digest(cls, value: str) -> str:
        return _digest(value)


@runtime_checkable
class LegacyMigrationCheckpointStore(Protocol):
    """Durable CAS state; it intentionally does not store source bodies."""

    async def load_or_create(
        self, *, checkpoint: LegacyMigrationCheckpoint
    ) -> LegacyMigrationCheckpoint:
        """Create an initial checkpoint or return the exact persisted one."""

    async def load(
        self, *, org_id: str, migration_id: str
    ) -> LegacyMigrationCheckpoint | None:
        """Load one tenant-scoped checkpoint."""

    async def compare_and_set(
        self,
        *,
        expected: LegacyMigrationCheckpoint,
        after_draft_id: str | None,
        status: LegacyMigrationStatus,
        report_digest: str | None,
        updated_at: datetime,
    ) -> LegacyMigrationCheckpoint | None:
        """Advance state iff the persisted revision still equals ``expected``."""


@runtime_checkable
class LegacyStageMigrationStore(Protocol):
    """Durable per-stage idempotency/mapping record for E2 D5.

    This deliberately has no method to return, approve, enqueue, or execute a
    legacy command.  Its only mutation is a source-bound outcome checkpoint.
    """

    async def load_or_create(
        self, *, record: LegacyStageMigrationRecord
    ) -> LegacyStageMigrationRecord:
        """Create or replay the exact source-bound record, otherwise fail closed."""

    async def load(
        self,
        *,
        org_id: str,
        migration_id: str,
        run_id: str,
        legacy_stage_id: str,
    ) -> LegacyStageMigrationRecord | None:
        """Load one tenant-scoped mapping/checkpoint."""

    async def replace_frozen(
        self, *, record: LegacyStageMigrationRecord
    ) -> LegacyStageMigrationRecord:
        """Replace only a prior frozen observation after a fresh safe scan.

        ``frozen_reconcile`` is intentionally not terminal migration truth. A
        later scan may observe that the old command is no longer claimed and
        may then create a separately held canonical stage. Adapters must
        reject replacement of every other outcome.
        """


def inventory_digest(
    *,
    org_id: str,
    source_complete: bool,
    drafts: tuple[LegacyDraftEvidence, ...],
    stages: tuple[LegacyStageEvidence, ...],
) -> str:
    """Hash every source fact in stable order; never include report timestamps."""

    return canonical_json_sha256(
        {
            "v": _VERSION,
            "org_id": _opaque(org_id),
            "source_complete": source_complete,
            "drafts": [
                item.model_dump(mode="json")
                for item in sorted(drafts, key=lambda item: item.draft_id)
            ],
            "stages": [
                item.model_dump(mode="json")
                for item in sorted(
                    stages, key=lambda item: (item.run_id, item.stage_id)
                )
            ],
        }
    )


def report_digest(
    *,
    migration_id: str,
    org_id: str,
    dry_run: bool,
    source_digest: str,
    migration_status: str,
    drafts_total: int,
    drafts_verified: int,
    drafts_pending: int,
    drafts_quarantined: int,
    stages_compatibility_only: int,
    stages_requiring_fresh_approval: int,
    stages_quarantined: int,
    source_complete: bool,
    audit_recorded: bool,
    cohort_ready: bool,
    blockers: tuple[str, ...],
) -> str:
    """Return the deterministic digest for a content-free migration report."""

    return canonical_json_sha256(
        {
            "v": _VERSION,
            "migration_id": _opaque(migration_id),
            "org_id": _opaque(org_id),
            "dry_run": dry_run,
            "source_digest": _digest(source_digest),
            "migration_status": migration_status,
            "drafts_total": drafts_total,
            "drafts_verified": drafts_verified,
            "drafts_pending": drafts_pending,
            "drafts_quarantined": drafts_quarantined,
            "stages_compatibility_only": stages_compatibility_only,
            "stages_requiring_fresh_approval": stages_requiring_fresh_approval,
            "stages_quarantined": stages_quarantined,
            "source_complete": source_complete,
            "audit_recorded": audit_recorded,
            "cohort_ready": cohort_ready,
            "blockers": sorted(set(blockers)),
        }
    )


__all__ = (
    "LegacyDraftDisposition",
    "LegacyDraftEvidence",
    "LegacyDraftVersionEvidence",
    "LegacyMigrationCheckpoint",
    "LegacyMigrationCheckpointStore",
    "LegacyMigrationInventory",
    "LegacyMigrationReport",
    "LegacyMigrationStateError",
    "LegacyMigrationStatus",
    "LegacyStageDisposition",
    "LegacyStageEvidence",
    "LegacyStageMigrationOutcome",
    "LegacyStageMigrationRecord",
    "LegacyStageMigrationStore",
    "inventory_digest",
    "report_digest",
)
