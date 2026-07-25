"""Durable, redacted state contracts for D12 repair-planning snapshots.

The D12 planner intentionally stops at a persisted *plan*.  These contracts
give a worker a restart-safe place to keep the trusted, redacted input snapshot
and its candidate/withheld outcomes without creating a cleanup, approval,
queue, or effect-execution capability.

Only :mod:`runtime_worker.jobs.repair_planning` is allowed to assemble a
snapshot from runtime ports.  This module is pure: it has no adapter, event,
queue, executor, filesystem, or network dependency.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Protocol, runtime_checkable

from pydantic import Field, field_validator

from agent_runtime.effects.claims import EffectClaimScanCursor
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.repair_reconciliation import (
    RepairDecision,
    RepairLegalHoldState,
    RepairPlan,
    RepairPlanCursor,
    RepairSnapshotRecord,
)


_OPAQUE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SNAPSHOT_VERSION = 1


class RepairPlanningStateError(RuntimeError):
    """Fail-closed durable-state error with no source detail in its text."""

    def __init__(self) -> None:
        super().__init__("repair planning state is unavailable")


def _opaque(value: str) -> str:
    if _OPAQUE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError("identifier must be a safe opaque token")
    return value


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


class RepairPlanningSnapshot(RuntimeContract):
    """One immutable, redacted source snapshot for a tenant.

    ``records`` are exclusively :class:`RepairSnapshotRecord` facts. They
    cannot contain a physical path, content body, raw reference, receipt, or
    target. ``source_complete`` means the bounded source *page* was collected
    without an adapter-boundary failure; a safe keyset page may still have a
    later page. The content-addressed snapshot identity deliberately excludes
    ``as_of`` so a restart or repeat poll observes the same durable cursor.
    """

    tenant_id: str = Field(min_length=1, max_length=256)
    snapshot_id: str = Field(min_length=1, max_length=256)
    snapshot_digest: str = Field(min_length=64, max_length=64)
    as_of: datetime
    source_complete: bool
    records: tuple[RepairSnapshotRecord, ...] = ()

    @field_validator("tenant_id", "snapshot_id")
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        return _opaque(value)

    @field_validator("snapshot_digest")
    @classmethod
    def _valid_digest(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("snapshot_digest must be sha256 hex")
        return value

    @field_validator("as_of")
    @classmethod
    def _valid_as_of(cls, value: datetime) -> datetime:
        return _aware_utc(value)

    @field_validator("records")
    @classmethod
    def _tenant_scoped_records(
        cls, value: tuple[RepairSnapshotRecord, ...], info
    ) -> tuple[RepairSnapshotRecord, ...]:
        tenant_id = info.data.get("tenant_id")
        if tenant_id is not None and any(
            record.tenant_id != tenant_id for record in value
        ):
            raise ValueError("snapshot records must stay tenant-scoped")
        return value

    def same_persisted_snapshot_as(self, other: "RepairPlanningSnapshot") -> bool:
        """Compare immutable source facts while intentionally ignoring ``as_of``.

        ``as_of`` records when one poll observed these facts; it is not part of
        the content-addressed snapshot identity.  A repeat poll therefore
        resumes the cursor and decisions from the first durable observation
        rather than treating the timestamp alone as a conflicting snapshot.
        The non-digest fields are checked too, so a manually forged model with
        a reused digest cannot replace the original persisted snapshot.
        """

        return (
            self.tenant_id == other.tenant_id
            and self.snapshot_id == other.snapshot_id
            and self.snapshot_digest == other.snapshot_digest
            and self.source_complete == other.source_complete
            and self.records == other.records
        )


class RepairPlanningSnapshotState(RuntimeContract):
    """Persisted cursor state for one immutable repair snapshot."""

    snapshot: RepairPlanningSnapshot
    after_candidate_id: str | None = Field(default=None, min_length=1, max_length=256)
    completed: bool = False

    @field_validator("after_candidate_id")
    @classmethod
    def _safe_after_candidate_id(cls, value: str | None) -> str | None:
        return None if value is None else _opaque(value)

    def cursor(self) -> RepairPlanCursor | None:
        """Return the safe exclusive cursor represented by persisted progress."""

        if self.after_candidate_id is None:
            return None
        return RepairPlanCursor(
            tenant_id=self.snapshot.tenant_id,
            snapshot_id=self.snapshot.snapshot_id,
            after_candidate_id=self.after_candidate_id,
        )


@runtime_checkable
class RepairPlanningSnapshotStore(Protocol):
    """CAS-backed durable state for a planning-only D12 worker.

    The store never accepts an effect command.  Its only mutation is recording
    the immutable snapshot and the safe decisions already derived from it.
    """

    async def load_or_create(
        self, *, snapshot: RepairPlanningSnapshot
    ) -> RepairPlanningSnapshotState:
        """Persist ``snapshot`` once or return its exact existing state."""

    async def load(
        self, *, tenant_id: str, snapshot_id: str
    ) -> RepairPlanningSnapshotState | None:
        """Read one tenant-scoped snapshot state."""

    async def advance(
        self,
        *,
        tenant_id: str,
        snapshot_id: str,
        expected_after_candidate_id: str | None,
        plan: RepairPlan,
    ) -> bool:
        """Atomically persist one exact plan page and advance its cursor.

        ``False`` means a concurrent/restarted runner advanced the cursor
        first; callers must reload and resume.  A malformed or conflicting
        state is a fail-closed :class:`RepairPlanningStateError`.
        """

    async def list_outcomes(
        self, *, tenant_id: str, snapshot_id: str
    ) -> Sequence[RepairDecision]:
        """Return only safe candidate/withheld decisions in stable order."""

    async def load_effect_claim_scan_cursor(self) -> EffectClaimScanCursor | None:
        """Read the durable global source cursor for the D12 claim scanner."""

    async def advance_effect_claim_scan_cursor(
        self,
        *,
        expected: EffectClaimScanCursor | None,
        next_cursor: EffectClaimScanCursor | None,
    ) -> bool:
        """Compare-and-swap the global source cursor after a full page persists."""


@runtime_checkable
class RepairLegalHoldLookup(Protocol):
    """Read a trusted legal-hold fact without exposing its source details."""

    async def resolve(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> RepairLegalHoldState:
        """Return ``NONE``, ``ACTIVE``, or fail closed as ``UNKNOWN``."""


def build_repair_planning_snapshot(
    *,
    tenant_id: str,
    records: Sequence[RepairSnapshotRecord],
    source_complete: bool,
    as_of: datetime,
) -> RepairPlanningSnapshot:
    """Build a deterministic snapshot identifier from redacted facts only."""

    safe_tenant_id = _opaque(tenant_id)
    ordered = tuple(sorted(records, key=lambda record: record.candidate_id))
    if any(record.tenant_id != safe_tenant_id for record in ordered):
        raise RepairPlanningStateError()
    candidate_ids = tuple(record.candidate_id for record in ordered)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise RepairPlanningStateError()
    canonical = {
        "v": _SNAPSHOT_VERSION,
        "tenant_id": safe_tenant_id,
        "source_complete": source_complete,
        "records": [record.model_dump(mode="json") for record in ordered],
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return RepairPlanningSnapshot(
        tenant_id=safe_tenant_id,
        snapshot_id=f"rps_{digest[:48]}",
        snapshot_digest=digest,
        as_of=_aware_utc(as_of),
        source_complete=source_complete,
        records=ordered,
    )


def validate_repair_plan_page(
    *,
    state: RepairPlanningSnapshotState,
    expected_after_candidate_id: str | None,
    plan: RepairPlan,
) -> None:
    """Prove a proposed page is an exact, monotonic slice of the snapshot.

    This guard lives at the state boundary too, not just in the runner.  A
    malformed caller therefore cannot overwrite an existing candidate with a
    different decision or skip a withheld row by advancing a cursor manually.
    """

    snapshot = state.snapshot
    if (
        plan.tenant_id != snapshot.tenant_id
        or plan.snapshot_id != snapshot.snapshot_id
        or expected_after_candidate_id != state.after_candidate_id
        or state.completed
    ):
        raise RepairPlanningStateError()
    remaining = tuple(
        record
        for record in snapshot.records
        if expected_after_candidate_id is None
        or record.candidate_id > expected_after_candidate_id
    )
    decision_ids = tuple(decision.candidate_id for decision in plan.decisions)
    expected_ids = tuple(
        record.candidate_id for record in remaining[: len(decision_ids)]
    )
    if not decision_ids and remaining:
        raise RepairPlanningStateError()
    if decision_ids != expected_ids:
        raise RepairPlanningStateError()
    has_more = len(remaining) > len(decision_ids)
    if plan.has_more != has_more:
        raise RepairPlanningStateError()
    expected_next = decision_ids[-1] if has_more and decision_ids else None
    actual_next = (
        plan.next_cursor.after_candidate_id if plan.next_cursor is not None else None
    )
    if actual_next != expected_next:
        raise RepairPlanningStateError()
    if plan.next_cursor is not None and (
        plan.next_cursor.tenant_id != snapshot.tenant_id
        or plan.next_cursor.snapshot_id != snapshot.snapshot_id
    ):
        raise RepairPlanningStateError()


__all__ = (
    "RepairPlanningSnapshot",
    "RepairPlanningSnapshotState",
    "RepairPlanningSnapshotStore",
    "RepairPlanningStateError",
    "RepairLegalHoldLookup",
    "build_repair_planning_snapshot",
    "validate_repair_plan_page",
)
