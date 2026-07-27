"""Narrow persistence ports for immutable run-control records."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_runtime.control_plane.contracts import (
    RunControlDecision,
    RunControlSnapshot,
)
from agent_runtime.execution.contracts import RuntimeContract


class RunControlSnapshotConflict(RuntimeError):
    """The same run was bound to a different semantic snapshot digest."""

    def __init__(self, *, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"run {run_id} already has a different control snapshot")


class RunControlScopeConflict(RuntimeError):
    """A persisted run-control record belongs to another subject scope."""

    def __init__(self, *, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"run {run_id} control state is outside the requested scope")


class RunControlDecisionConflict(RuntimeError):
    """A stable decision id was reused for a different semantic body."""

    def __init__(self, *, run_id: str, decision_id: str) -> None:
        self.run_id = run_id
        self.decision_id = decision_id
        super().__init__(f"run {run_id} decision {decision_id} conflicts")


class RunControlJournalCorruption(RuntimeError):
    """Canonical control events cannot be folded into one valid state."""

    def __init__(self, *, run_id: str, reason: str) -> None:
        self.run_id = run_id
        self.reason = reason
        super().__init__(f"run {run_id} control journal is invalid: {reason}")


class RunControlSnapshotWrite(RuntimeContract):
    """Verified transport facts needed to append the snapshot event."""

    org_id: str
    trace_id: str
    snapshot: RunControlSnapshot


class RunControlDecisionWrite(RuntimeContract):
    """Verified scope and trace facts needed to append a decision event."""

    org_id: str
    trace_id: str
    subject_fingerprint: str
    decision: RunControlDecision


class SequencedRunControlDecision(RuntimeContract):
    """One decision plus its canonical run-event sequence."""

    sequence_no: int
    decision: RunControlDecision


@runtime_checkable
class RunControlSnapshotStorePort(Protocol):
    """Atomic immutable snapshot bind and scoped replay."""

    async def get(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
    ) -> RunControlSnapshot | None: ...

    async def get_or_create(
        self,
        write: RunControlSnapshotWrite,
    ) -> RunControlSnapshot: ...


@runtime_checkable
class RunControlDecisionStorePort(Protocol):
    """Append-only feature-decision lineage on the canonical run journal."""

    async def append(
        self,
        write: RunControlDecisionWrite,
    ) -> SequencedRunControlDecision: ...

    async def list_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        after_sequence: int = 0,
    ) -> tuple[SequencedRunControlDecision, ...]: ...


__all__ = [
    "RunControlDecisionStorePort",
    "RunControlDecisionConflict",
    "RunControlDecisionWrite",
    "RunControlJournalCorruption",
    "RunControlScopeConflict",
    "RunControlSnapshotConflict",
    "RunControlSnapshotStorePort",
    "RunControlSnapshotWrite",
    "SequencedRunControlDecision",
]
