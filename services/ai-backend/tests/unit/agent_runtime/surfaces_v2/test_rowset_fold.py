"""Pure StagedWriteFold tests for bulk row-sets (PRD-D3).

Drives the fold with raw event dicts (the store shape) and asserts: rows +
stances + counts fold from ``write.staged`` / ``revision.added`` / row-scoped
``decision.recorded``; agent pre-holds are sticky across a user override; the
apply decision freezes to APPLY_PENDING; the terminal ``write.applied`` folds to
APPLIED / PARTIALLY_APPLIED / (failed ⇒) STAGED; a re-fold after "restart" is
identical; and last-decision-wins per row_key.
"""

from __future__ import annotations

from agent_runtime.surfaces_v2.rowset import RowStance
from agent_runtime.surfaces_v2.staging import StagedWriteFold, StagedWriteStatus

_STAGE = "stage_bulk"
_SURF = "surf_bulk"


def _staged(seq: int, *, rows: int, holds=None) -> dict:
    return {
        "event_type": "write.staged",
        "sequence_no": seq,
        "payload": {
            "v": 1,
            "stage_id": _STAGE,
            "surface_id": _SURF,
            "target": {"connector": "linear", "op": "update_issue"},
            "proposal_ref": f"stage://{_STAGE}/v1",
            "rows": rows,
            "agent_holds": holds or [],
        },
    }


def _rowset_rev(seq: int, keys: list[str]) -> dict:
    return {
        "event_type": "revision.added",
        "sequence_no": seq,
        "payload": {
            "v": 1,
            "stage_id": _STAGE,
            "rev": 1,
            "author": "agent",
            "diff_ref": f"stage://{_STAGE}/v1",
            "proposal_ref": f"stage://{_STAGE}/v1",
            "rowset": {
                "rows": [
                    {
                        "row_key": k,
                        "title": f"Row {k}",
                        "target_args": {"id": k, "priority": 2},
                        "changes": [{"field": "priority", "old": 1, "new": 2}],
                    }
                    for k in keys
                ]
            },
        },
    }


def _row_decision(seq: int, decision: str, keys: list[str], *, actor="user") -> dict:
    return {
        "event_type": "decision.recorded",
        "sequence_no": seq,
        "payload": {
            "v": 1,
            "stage_id": _STAGE,
            "decision": decision,
            "scope": {"row_keys": keys},
            "actor": actor,
        },
    }


def _apply_decision(seq: int, keys: list[str], *, actor="user") -> dict:
    return {
        "event_type": "decision.recorded",
        "sequence_no": seq,
        "payload": {
            "v": 1,
            "stage_id": _STAGE,
            "decision": "approve",
            "scope": {"row_keys": keys},
            "actor": actor,
            "apply": True,
        },
    }


def _applied(seq: int, result: str, keys: list[str], row_results: list[dict]) -> dict:
    return {
        "event_type": "write.applied",
        "sequence_no": seq,
        "payload": {
            "v": 1,
            "stage_id": _STAGE,
            "rev": 1,
            "result": result,
            "row_keys": keys,
            "row_results": row_results,
        },
    }


def _fold(events):  # noqa: ANN001
    return StagedWriteFold.fold_raw(events)[_STAGE]


class TestRowsetFold:
    def test_staged_and_rev_fold_rows_and_default_stances(self) -> None:
        state = _fold([_staged(1, rows=3), _rowset_rev(2, ["a", "b", "c"])])
        assert state.is_rowset()
        assert [r.row_key for r in state.rows] == ["a", "b", "c"]
        assert all(r.stance is RowStance.WILL_APPLY for r in state.rows)
        assert state.row_counts.total == 3
        assert state.row_counts.will_apply == 3
        assert state.row_counts.held == 0

    def test_agent_pre_holds_are_held_and_reason_sticky_across_override(self) -> None:
        events = [
            _staged(1, rows=3, holds=[{"row_key": "b", "reason": "recent reply"}]),
            _rowset_rev(2, ["a", "b", "c"]),
        ]
        state = _fold(events)
        b = next(r for r in state.rows if r.row_key == "b")
        assert b.stance is RowStance.HELD
        assert b.agent_hold_reason == "recent reply"
        assert b.decided_by == "agent"
        assert state.row_counts.held == 1

        # User overrides row b to approve — stance flips, reason STAYS (FR-C7).
        state = _fold([*events, _row_decision(3, "approve", ["b"])])
        b = next(r for r in state.rows if r.row_key == "b")
        assert b.stance is RowStance.WILL_APPLY
        assert b.agent_hold_reason == "recent reply"  # sticky
        assert b.decided_by == "user"
        assert state.row_counts.will_apply == 3

    def test_last_decision_wins_per_row_key(self) -> None:
        events = [
            _staged(1, rows=2),
            _rowset_rev(2, ["a", "b"]),
            _row_decision(3, "hold", ["a"]),
            _row_decision(4, "approve", ["a"]),
        ]
        state = _fold(events)
        a = next(r for r in state.rows if r.row_key == "a")
        assert a.stance is RowStance.WILL_APPLY

    def test_apply_decision_freezes_to_apply_pending(self) -> None:
        events = [
            _staged(1, rows=2),
            _rowset_rev(2, ["a", "b"]),
            _apply_decision(3, ["a", "b"]),
        ]
        state = _fold(events)
        assert state.status is StagedWriteStatus.APPLY_PENDING
        assert set(state.will_apply_keys()) == {"a", "b"}

    def test_applied_terminal_sets_outcomes_and_status(self) -> None:
        events = [
            _staged(1, rows=2),
            _rowset_rev(2, ["a", "b"]),
            _apply_decision(3, ["a", "b"]),
            _applied(
                4,
                "applied",
                ["a", "b"],
                [
                    {"row_key": "a", "outcome": "applied"},
                    {"row_key": "b", "outcome": "applied"},
                ],
            ),
        ]
        state = _fold(events)
        assert state.status is StagedWriteStatus.APPLIED
        assert state.row_counts.applied == 2
        assert state.row_counts.failed == 0

    def test_partial_terminal_sets_partially_applied_and_per_row_outcomes(self) -> None:
        events = [
            _staged(1, rows=2),
            _rowset_rev(2, ["a", "b"]),
            _apply_decision(3, ["a", "b"]),
            _applied(
                4,
                "partial",
                ["a", "b"],
                [
                    {"row_key": "a", "outcome": "applied"},
                    {"row_key": "b", "outcome": "failed"},
                ],
            ),
        ]
        state = _fold(events)
        assert state.status is StagedWriteStatus.PARTIALLY_APPLIED
        assert state.row_counts.applied == 1
        assert state.row_counts.failed == 1
        a = next(r for r in state.rows if r.row_key == "a")
        b = next(r for r in state.rows if r.row_key == "b")
        assert a.apply_outcome == "applied"
        assert b.apply_outcome == "failed"

    def test_all_failed_terminal_returns_to_staged_apply_consumed(self) -> None:
        events = [
            _staged(1, rows=2),
            _rowset_rev(2, ["a", "b"]),
            _apply_decision(3, ["a", "b"]),
            _applied(4, "failed", ["a", "b"], []),
        ]
        state = _fold(events)
        assert state.status is StagedWriteStatus.STAGED
        # Stances intact (a fresh apply may retry); no outcomes recorded.
        assert set(state.will_apply_keys()) == {"a", "b"}
        assert state.row_counts.applied == 0
        assert state.row_counts.failed == 0

    def test_refold_after_restart_is_identical(self) -> None:
        events = [
            _staged(1, rows=3, holds=[{"row_key": "c", "reason": "unsure"}]),
            _rowset_rev(2, ["a", "b", "c"]),
            _row_decision(3, "approve", ["c"]),
            _apply_decision(4, ["a", "b", "c"]),
            _applied(
                5,
                "applied",
                ["a", "b", "c"],
                [{"row_key": k, "outcome": "applied"} for k in ("a", "b", "c")],
            ),
        ]
        first = _fold(events)
        second = _fold(list(reversed(events)))  # order-independent (sorted by seq)
        assert first == second

    def test_write_applied_without_apply_pending_folds_corrupt(self) -> None:
        # A forged terminal onto a not-yet-frozen row-set never masquerades as a send.
        events = [
            _staged(1, rows=1),
            _rowset_rev(2, ["a"]),
            _applied(3, "applied", ["a"], [{"row_key": "a", "outcome": "applied"}]),
        ]
        state = _fold(events)
        assert state.status is StagedWriteStatus.CORRUPT
