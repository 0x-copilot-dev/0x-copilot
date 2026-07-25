"""In-process parity adapter for D12 repair-planning snapshot state."""

from __future__ import annotations

from collections.abc import Sequence
from threading import RLock

from agent_runtime.effects.claims import EffectClaimScanCursor
from agent_runtime.surfaces_v2.repair_planning import (
    RepairPlanningSnapshot,
    RepairPlanningSnapshotState,
    RepairPlanningStateError,
    validate_repair_plan_page,
)
from agent_runtime.surfaces_v2.repair_reconciliation import RepairDecision, RepairPlan


class InMemoryRepairPlanningSnapshotStore:
    """One-process semantic parity store used by tests and local development.

    It intentionally has no restart guarantee; the file and Postgres adapters
    provide the durable variants.  Its compare-and-swap behavior is identical
    while the process remains alive, which makes replay/cursor tests portable.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._states: dict[tuple[str, str], RepairPlanningSnapshotState] = {}
        self._outcomes: dict[tuple[str, str], dict[str, RepairDecision]] = {}
        self._effect_claim_scan_cursor: EffectClaimScanCursor | None = None

    async def load_or_create(
        self, *, snapshot: RepairPlanningSnapshot
    ) -> RepairPlanningSnapshotState:
        key = (snapshot.tenant_id, snapshot.snapshot_id)
        with self._lock:
            existing = self._states.get(key)
            if existing is None:
                state = RepairPlanningSnapshotState(snapshot=snapshot)
                self._states[key] = state
                self._outcomes[key] = {}
                return state
            if not existing.snapshot.same_persisted_snapshot_as(snapshot):
                raise RepairPlanningStateError()
            return existing

    async def load(
        self, *, tenant_id: str, snapshot_id: str
    ) -> RepairPlanningSnapshotState | None:
        with self._lock:
            return self._states.get((tenant_id, snapshot_id))

    async def advance(
        self,
        *,
        tenant_id: str,
        snapshot_id: str,
        expected_after_candidate_id: str | None,
        plan: RepairPlan,
    ) -> bool:
        key = (tenant_id, snapshot_id)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                raise RepairPlanningStateError()
            if state.after_candidate_id != expected_after_candidate_id:
                return False
            validate_repair_plan_page(
                state=state,
                expected_after_candidate_id=expected_after_candidate_id,
                plan=plan,
            )
            outcomes = self._outcomes[key]
            for decision in plan.decisions:
                existing = outcomes.get(decision.candidate_id)
                if existing is not None and existing != decision:
                    raise RepairPlanningStateError()
                outcomes[decision.candidate_id] = decision
            next_after = (
                plan.next_cursor.after_candidate_id
                if plan.next_cursor is not None
                else (plan.decisions[-1].candidate_id if plan.decisions else None)
            )
            self._states[key] = RepairPlanningSnapshotState(
                snapshot=state.snapshot,
                after_candidate_id=next_after,
                completed=not plan.has_more,
            )
            return True

    async def list_outcomes(
        self, *, tenant_id: str, snapshot_id: str
    ) -> Sequence[RepairDecision]:
        with self._lock:
            outcomes = self._outcomes.get((tenant_id, snapshot_id))
            if outcomes is None:
                return ()
            return tuple(outcomes[key] for key in sorted(outcomes))

    async def load_effect_claim_scan_cursor(self) -> EffectClaimScanCursor | None:
        with self._lock:
            return self._effect_claim_scan_cursor

    async def advance_effect_claim_scan_cursor(
        self,
        *,
        expected: EffectClaimScanCursor | None,
        next_cursor: EffectClaimScanCursor | None,
    ) -> bool:
        with self._lock:
            if self._effect_claim_scan_cursor != expected:
                return False
            self._effect_claim_scan_cursor = next_cursor
            return True


__all__ = ("InMemoryRepairPlanningSnapshotStore",)
