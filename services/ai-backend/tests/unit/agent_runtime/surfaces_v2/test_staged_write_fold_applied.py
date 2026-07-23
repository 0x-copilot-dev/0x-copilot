"""PRD-D2 fold state-machine tests for ``write.applied`` (extends the D1 fold).

``APPROVED (rev N)`` + ``applied {rev N}`` ⇒ APPLIED (terminal); ``APPROVED`` +
``failed {rev N}`` ⇒ STAGED with ``approved_rev`` cleared (held, approval consumed);
a mismatched / non-approved ``write.applied`` ⇒ CORRUPT (defensive).
"""

from __future__ import annotations

import pytest

from agent_runtime.surfaces_v2.staging import (
    StagedWriteFold,
    StagedWriteStatus,
    WriteStager,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_STAGE = "stage_1"
_DRAFT = "d" * 32


def _base_events() -> list[dict[str, object]]:
    """An approved stage on rev 1 (write.staged → revision → approve)."""

    return [
        {
            "event_type": "write.staged",
            "sequence_no": 10,
            "payload": {
                "v": 1,
                "stage_id": _STAGE,
                "surface_id": "surf_1",
                "target": {"connector": "gmail", "op": "send"},
                "proposal_ref": f"draft://{_DRAFT}/v1",
            },
        },
        {
            "event_type": "revision.added",
            "sequence_no": 11,
            "payload": {
                "v": 1,
                "stage_id": _STAGE,
                "rev": 1,
                "author": "agent",
                "proposal_ref": f"draft://{_DRAFT}/v1",
                "diff_ref": f"draft://{_DRAFT}/v1..v1",
            },
        },
        {
            "event_type": "decision.recorded",
            "sequence_no": 12,
            "payload": {
                "v": 1,
                "stage_id": _STAGE,
                "decision": "approve",
                "scope": {"rev": 1},
                "actor": "user",
            },
        },
    ]


def _applied(result: str, *, rev: int = 1, seq: int = 20, failure: str | None = None):
    payload: dict[str, object] = {
        "v": 1,
        "stage_id": _STAGE,
        "rev": rev,
        "result": result,
    }
    if failure is not None:
        payload["failure"] = {"code": failure}
    return {"event_type": "write.applied", "sequence_no": seq, "payload": payload}


class TestAppliedTerminal:
    def test_applied_folds_to_applied_terminal(self) -> None:
        states = StagedWriteFold.fold_raw([*_base_events(), _applied("applied")])
        state = states[_STAGE]
        assert state.status is StagedWriteStatus.APPLIED
        assert state.apply_result == "applied"
        assert state.approved_rev == 1

    async def test_further_decisions_after_applied_are_frozen_409(self) -> None:
        # A stage folded to APPLIED is terminal: the WriteStager decision matrix
        # freezes it (409 stage_frozen) — no re-approve, no reject.
        from agent_runtime.surfaces_v2.staging import StageFrozen

        states = StagedWriteFold.fold_raw([*_base_events(), _applied("applied")])
        state = states[_STAGE]
        # Directly exercise the matrix cell the route uses.
        with pytest.raises(StageFrozen):
            WriteStager._decide_approve(state, rev=1)
        with pytest.raises(StageFrozen):
            WriteStager._decide_reject(state, rev=1)


class TestFailedHeld:
    def test_failed_folds_back_to_staged_approval_consumed(self) -> None:
        states = StagedWriteFold.fold_raw(
            [*_base_events(), _applied("failed", failure="precondition_drift")]
        )
        state = states[_STAGE]
        # Held: back to STAGED, approval consumed (approved_rev cleared) — a fresh
        # approve is required to retry.
        assert state.status is StagedWriteStatus.STAGED
        assert state.approved_rev is None
        assert state.apply_result == "failed"
        assert state.apply_failure_code == "precondition_drift"

    async def test_failed_allows_a_fresh_approve(self) -> None:
        states = StagedWriteFold.fold_raw(
            [*_base_events(), _applied("failed", failure="connector_error")]
        )
        state = states[_STAGE]
        # A fresh approve of the latest rev is allowed again (retry path).
        assert WriteStager._decide_approve(state, rev=state.latest_rev) == 1


class TestDefensiveCorrupt:
    def test_applied_on_unapproved_stage_folds_corrupt(self) -> None:
        # write.applied without a prior approve ⇒ CORRUPT (unreachable absent a bug).
        events = [e for e in _base_events() if e["event_type"] != "decision.recorded"]
        states = StagedWriteFold.fold_raw([*events, _applied("applied")])
        assert states[_STAGE].status is StagedWriteStatus.CORRUPT

    def test_applied_on_mismatched_rev_folds_corrupt(self) -> None:
        # Approved rev 1, but write.applied claims rev 2 ⇒ CORRUPT.
        states = StagedWriteFold.fold_raw([*_base_events(), _applied("applied", rev=2)])
        assert states[_STAGE].status is StagedWriteStatus.CORRUPT

    def test_unknown_result_folds_corrupt(self) -> None:
        states = StagedWriteFold.fold_raw([*_base_events(), _applied("weird")])
        assert states[_STAGE].status is StagedWriteStatus.CORRUPT


class TestReplayDeterminism:
    def test_fold_is_order_independent(self) -> None:
        events = [*_base_events(), _applied("applied")]
        forward = StagedWriteFold.fold_raw(events)
        shuffled = StagedWriteFold.fold_raw(list(reversed(events)))
        assert (
            forward[_STAGE].status
            is shuffled[_STAGE].status
            is (StagedWriteStatus.APPLIED)
        )
