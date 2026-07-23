"""Pure StagedWriteFold tests (PRD-D1).

The fold is a deterministic, total projection of a run's ledger events into
per-stage :class:`StagedWriteState`. These tests drive it with raw event dicts
(the shape the store returns) and assert: a full stage/revise/decide sequence
folds to the expected state; interleaved non-stage events are tolerated; a
re-fold after "restart" is identical; and ``write.applied`` (D2's event) folds
to APPLIED for forward-compat.
"""

from __future__ import annotations

from agent_runtime.surfaces_v2.staging import (
    StagedWriteFold,
    StagedWriteStatus,
)


def _staged(seq: int, *, stage="stage_1", surface="surf_1") -> dict:
    return {
        "event_type": "write.staged",
        "sequence_no": seq,
        "payload": {
            "v": 1,
            "stage_id": stage,
            "surface_id": surface,
            "target": {"connector": "gmail", "op": "send"},
            "proposal_ref": "draft://abcdef0123456789abcdef0123456789/v1",
        },
    }


def _revision(seq: int, rev: int, author: str, *, stage="stage_1", spans=None) -> dict:
    return {
        "event_type": "revision.added",
        "sequence_no": seq,
        "payload": {
            "v": 1,
            "stage_id": stage,
            "rev": rev,
            "author": author,
            "diff_ref": f"draft://abcdef0123456789abcdef0123456789/v1..v{rev}",
            "proposal_ref": f"draft://abcdef0123456789abcdef0123456789/v{rev}",
            "authorship_spans": spans or [],
        },
    }


def _decision(seq: int, decision: str, rev: int, *, stage="stage_1") -> dict:
    return {
        "event_type": "decision.recorded",
        "sequence_no": seq,
        "payload": {
            "v": 1,
            "stage_id": stage,
            "decision": decision,
            "scope": {"rev": rev},
            "actor": "user",
        },
    }


class TestStagedWriteFold:
    def test_stage_then_two_revisions_then_approve(self) -> None:
        events = [
            _staged(10),
            _revision(11, 1, "agent"),
            _revision(12, 2, "user", spans=[{"start": 0, "end": 4, "author": "user"}]),
            _decision(13, "approve", 2),
        ]
        states = StagedWriteFold.fold(_typed(events))
        state = states["stage_1"]
        assert state.status is StagedWriteStatus.APPROVED
        assert state.latest_rev == 2
        assert state.approved_rev == 2
        assert len(state.revisions) == 2
        assert state.revisions[1].author == "user"
        assert state.revisions[1].authorship_spans[0].start == 0
        assert state.decisions[-1].decision == "approve"
        assert state.draft_id == "abcdef0123456789abcdef0123456789"
        assert state.target_connector == "gmail"

    def test_reject_then_restore_returns_to_staged(self) -> None:
        events = [
            _staged(10),
            _revision(11, 1, "agent"),
            _decision(12, "reject", 1),
            _decision(13, "restore", 1),
        ]
        state = StagedWriteFold.fold(_typed(events))["stage_1"]
        assert state.status is StagedWriteStatus.STAGED
        assert state.approved_rev is None

    def test_tolerates_interleaved_non_stage_events(self) -> None:
        events = [
            {"event_type": "read.executed", "sequence_no": 5, "payload": {"v": 1}},
            _staged(10),
            {"event_type": "usage.recorded", "sequence_no": 11, "payload": {"v": 1}},
            _revision(12, 1, "agent"),
        ]
        states = StagedWriteFold.fold(_typed(events))
        assert set(states.keys()) == {"stage_1"}
        assert states["stage_1"].latest_rev == 1

    def test_out_of_order_events_are_sorted_by_sequence_no(self) -> None:
        events = [
            _revision(12, 2, "user"),
            _decision(13, "approve", 2),
            _staged(10),
            _revision(11, 1, "agent"),
        ]
        state = StagedWriteFold.fold(_typed(events))["stage_1"]
        assert state.latest_rev == 2
        assert state.status is StagedWriteStatus.APPROVED

    def test_refold_is_identical(self) -> None:
        events = _typed(
            [_staged(10), _revision(11, 1, "agent"), _decision(12, "reject", 1)]
        )
        first = StagedWriteFold.fold(events)["stage_1"]
        second = StagedWriteFold.fold(events)["stage_1"]
        assert first == second

    def test_write_applied_folds_to_applied_forward_compat(self) -> None:
        events = _typed(
            [
                _staged(10),
                _revision(11, 1, "agent"),
                _decision(12, "approve", 1),
                {
                    "event_type": "write.applied",
                    "sequence_no": 13,
                    "payload": {
                        "v": 1,
                        "stage_id": "stage_1",
                        "rev": 1,
                        "result": "applied",
                    },
                },
            ]
        )
        state = StagedWriteFold.fold(events)["stage_1"]
        assert state.status is StagedWriteStatus.APPLIED

    def test_revision_for_unknown_stage_is_ignored(self) -> None:
        events = _typed([_revision(11, 1, "agent", stage="ghost")])
        assert StagedWriteFold.fold(events) == {}

    def test_multiple_stages_fold_independently(self) -> None:
        events = _typed(
            [
                _staged(10, stage="s1", surface="f1"),
                _staged(11, stage="s2", surface="f2"),
                _revision(12, 1, "agent", stage="s1"),
                _revision(13, 1, "agent", stage="s2"),
                _decision(14, "reject", 1, stage="s2"),
            ]
        )
        states = StagedWriteFold.fold(events)
        assert states["s1"].status is StagedWriteStatus.STAGED
        assert states["s2"].status is StagedWriteStatus.REJECTED


class _Envelope:
    """Minimal envelope-like object with the three fields the fold reads."""

    def __init__(self, raw: dict) -> None:
        self.event_type = raw["event_type"]
        self.sequence_no = raw["sequence_no"]
        self.payload = raw["payload"]


def _typed(raws: list[dict]) -> list[_Envelope]:
    return [_Envelope(raw) for raw in raws]
