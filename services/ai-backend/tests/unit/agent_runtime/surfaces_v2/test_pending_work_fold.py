"""Pure PendingWorkFold tests (PRD-E2).

The fold is a deterministic, total projection of ONE run's ledger events into
the pending items that run contributes to the cross-run queue. These pin the §5
pending predicate — one definition, tested against hand-built coherent sequences
AND the shared A1 golden fixture (the referee the TypeScript twin also folds):

* an open gate is pending until its matching ``gate.resolved`` (either outcome);
* a single-artifact stage is pending only while ``STAGED``;
* a row-set is pending while ``STAGED`` and some row is undecided-by-the-user;
* every prefix of the golden sequence folds to exactly the incremental pending
  set (the DoD "cards appear/disappear exactly with ledger state", server side);
* malformed / interleaved / orphan events are tolerated, never raised.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_runtime.surfaces_v2.pending_work import (
    PendingItemKind,
    PendingWorkFold,
)

_RUN = "run0abcdef0123456789abcdef01234567"
_CONV = "conv_1"


# ---------------------------------------------------------------------------
# Event builders (raw dict shape the store returns)
# ---------------------------------------------------------------------------


def _gate_opened(
    seq: int, *, gate_id: str, connector="linear", purpose="to read ENG-1"
) -> dict:
    return {
        "event_type": "gate.opened",
        "sequence_no": seq,
        "payload": {
            "v": 1,
            "gate_id": gate_id,
            "connector": connector,
            "purpose": purpose,
            "scopes": ["read:issues"],
            "auth_state": "missing",
        },
    }


def _gate_resolved(seq: int, *, gate_id: str, outcome="connected") -> dict:
    return {
        "event_type": "gate.resolved",
        "sequence_no": seq,
        "payload": {"v": 1, "gate_id": gate_id, "outcome": outcome},
    }


def _staged(
    seq: int, *, stage="stage_1", surface="surf_1", rows=None, holds=None
) -> dict:
    payload: dict[str, Any] = {
        "v": 1,
        "stage_id": stage,
        "surface_id": surface,
        "target": {"connector": "gmail", "op": "send"},
        "proposal_ref": "draft://abcdef0123456789abcdef0123456789/v1",
    }
    if rows is not None:
        payload["rows"] = rows
        payload["proposal_ref"] = f"stage://{stage}/v1"
    if holds is not None:
        payload["agent_holds"] = holds
    return {"event_type": "write.staged", "sequence_no": seq, "payload": payload}


def _revision(seq: int, rev: int, author: str, *, stage="stage_1") -> dict:
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
            "authorship_spans": [],
        },
    }


def _rowset_rev(seq: int, keys: list[str], *, stage="stage_r") -> dict:
    return {
        "event_type": "revision.added",
        "sequence_no": seq,
        "payload": {
            "v": 1,
            "stage_id": stage,
            "rev": 1,
            "author": "agent",
            "diff_ref": f"stage://{stage}/v1",
            "proposal_ref": f"stage://{stage}/v1",
            "rowset": {
                "rows": [
                    {
                        "row_key": k,
                        "title": f"Row {k}",
                        "target_args": {"id": k},
                        "changes": [{"field": "priority", "old": 1, "new": 2}],
                    }
                    for k in keys
                ]
            },
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


def _row_decision(seq: int, decision: str, keys: list[str], *, stage="stage_r") -> dict:
    return {
        "event_type": "decision.recorded",
        "sequence_no": seq,
        "payload": {
            "v": 1,
            "stage_id": stage,
            "decision": decision,
            "scope": {"row_keys": keys},
            "actor": "user",
        },
    }


def _applied(seq: int, rev: int, *, stage="stage_1") -> dict:
    return {
        "event_type": "write.applied",
        "sequence_no": seq,
        "payload": {"v": 1, "stage_id": stage, "rev": rev, "result": "applied"},
    }


def _pending_keys(events) -> list[tuple[str, str]]:
    return sorted(
        (item.item_kind.value, item.gate_id or item.stage_id or "")
        for item in PendingWorkFold.fold_raw(events)
    )


# ---------------------------------------------------------------------------
# Gate predicate
# ---------------------------------------------------------------------------


class TestGatePending:
    def test_open_gate_is_pending(self) -> None:
        items = PendingWorkFold.fold_raw([_gate_opened(1, gate_id="g1")])
        assert len(items) == 1
        item = items[0]
        assert item.item_kind is PendingItemKind.GATE
        assert item.gate_id == "g1"
        assert item.title == "to read ENG-1"
        assert item.connector == "linear"
        assert item.op is None
        assert item.opened_sequence_no == 1
        assert item.rows_pending is None

    def test_resolved_connected_gate_absent(self) -> None:
        events = [
            _gate_opened(1, gate_id="g1"),
            _gate_resolved(2, gate_id="g1", outcome="connected"),
        ]
        assert PendingWorkFold.fold_raw(events) == ()

    def test_resolved_cancelled_gate_absent(self) -> None:
        events = [
            _gate_opened(1, gate_id="g1"),
            _gate_resolved(2, gate_id="g1", outcome="cancelled"),
        ]
        assert PendingWorkFold.fold_raw(events) == ()

    def test_gate_resolved_without_opened_ignored(self) -> None:
        assert PendingWorkFold.fold_raw([_gate_resolved(1, gate_id="ghost")]) == ()

    def test_two_open_gates_both_pending(self) -> None:
        events = [_gate_opened(1, gate_id="g1"), _gate_opened(3, gate_id="g2")]
        assert _pending_keys(events) == [("gate", "g1"), ("gate", "g2")]


# ---------------------------------------------------------------------------
# Single-artifact stage predicate
# ---------------------------------------------------------------------------


class TestStagePending:
    def _staged_seq(self, extra: list[dict] | None = None) -> list[dict]:
        events = [_staged(10), _revision(11, 1, "agent")]
        if extra:
            events.extend(extra)
        return events

    def test_staged_stage_is_pending(self) -> None:
        items = PendingWorkFold.fold_raw(self._staged_seq())
        assert len(items) == 1
        item = items[0]
        assert item.item_kind is PendingItemKind.STAGED_WRITE
        assert item.stage_id == "stage_1"
        assert item.surface_id == "surf_1"
        assert item.connector == "gmail"
        assert item.op == "send"
        assert item.title == "gmail · send"
        assert item.opened_sequence_no == 10
        assert item.rows_pending is None
        assert item.rows_total is None

    def test_approved_stage_absent(self) -> None:
        events = self._staged_seq([_decision(12, "approve", 1)])
        assert PendingWorkFold.fold_raw(events) == ()

    def test_rejected_stage_absent(self) -> None:
        events = self._staged_seq([_decision(12, "reject", 1)])
        assert PendingWorkFold.fold_raw(events) == ()

    def test_applied_stage_absent(self) -> None:
        events = self._staged_seq([_decision(12, "approve", 1), _applied(13, 1)])
        assert PendingWorkFold.fold_raw(events) == ()

    def test_restore_returns_rejected_stage_to_pending(self) -> None:
        events = self._staged_seq(
            [_decision(12, "reject", 1), _decision(13, "restore", 1)]
        )
        keys = _pending_keys(events)
        assert keys == [("staged_write", "stage_1")]


# ---------------------------------------------------------------------------
# Row-set predicate (D3 accounting reused, not re-derived)
# ---------------------------------------------------------------------------


class TestRowSetPending:
    def _base(self, holds=None) -> list[dict]:
        return [
            _staged(10, stage="stage_r", surface="surf_r", rows=3, holds=holds or []),
            _rowset_rev(11, ["a", "b", "c"]),
        ]

    def test_fresh_rowset_all_rows_pending(self) -> None:
        items = PendingWorkFold.fold_raw(self._base())
        assert len(items) == 1
        item = items[0]
        assert item.stage_id == "stage_r"
        assert item.rows_total == 3
        assert item.rows_pending == 3

    def test_partial_user_decisions_reduce_pending_count(self) -> None:
        # User approves a, holds b; c stays undecided → 1 still pending.
        events = self._base() + [
            _row_decision(12, "approve", ["a"]),
            _row_decision(13, "hold", ["b"]),
        ]
        items = PendingWorkFold.fold_raw(events)
        assert len(items) == 1
        assert items[0].rows_total == 3
        assert items[0].rows_pending == 1

    def test_agent_preheld_row_counts_as_pending(self) -> None:
        # An agent pre-hold is NOT a user decision — the row still waits (FR-C7).
        events = self._base(holds=[{"row_key": "b", "reason": "ambiguous"}])
        item = PendingWorkFold.fold_raw(events)[0]
        assert item.rows_pending == 3  # a, b (agent-held), c all await the user

    def test_all_rows_user_decided_not_pending(self) -> None:
        events = self._base() + [_row_decision(12, "hold", ["a", "b", "c"])]
        # Every row decided-by-user → 0 pending → the stage drops from the queue.
        assert PendingWorkFold.fold_raw(events) == ()

    def test_applied_rowset_absent(self) -> None:
        events = self._base() + [
            _row_decision(12, "approve", ["a", "b", "c"]),
            {
                "event_type": "decision.recorded",
                "sequence_no": 13,
                "payload": {
                    "v": 1,
                    "stage_id": "stage_r",
                    "decision": "approve",
                    "scope": {"row_keys": ["a", "b", "c"]},
                    "actor": "user",
                    "apply": True,
                },
            },
        ]
        # apply decision freezes to APPLY_PENDING → no longer waiting on the user.
        assert PendingWorkFold.fold_raw(events) == ()


# ---------------------------------------------------------------------------
# Adversarial + tolerance
# ---------------------------------------------------------------------------


class TestFoldTolerance:
    def test_malformed_payloads_skipped_never_raise(self) -> None:
        events = [
            {"event_type": "gate.opened", "sequence_no": 1, "payload": None},
            {"event_type": "gate.opened", "sequence_no": 2, "payload": {"v": 1}},
            {"event_type": "write.staged", "sequence_no": 3, "payload": "nope"},
            _gate_opened(4, gate_id="g_real"),
        ]
        keys = _pending_keys(events)
        assert keys == [("gate", "g_real")]

    def test_interleaved_non_v2_events_tolerated(self) -> None:
        events = [
            {"event_type": "model_delta", "sequence_no": 1, "payload": {"text": "hi"}},
            _gate_opened(2, gate_id="g1"),
            {"event_type": "tool_result", "sequence_no": 3, "payload": {}},
            _staged(4),
            _revision(5, 1, "agent"),
        ]
        assert _pending_keys(events) == [("gate", "g1"), ("staged_write", "stage_1")]

    def test_out_of_order_events_sorted_by_sequence(self) -> None:
        events = [
            _gate_resolved(2, gate_id="g1"),
            _gate_opened(1, gate_id="g1"),
        ]
        # Resolve arrives first in the list but seq 2 > 1 → gate is resolved.
        assert PendingWorkFold.fold_raw(events) == ()


# ---------------------------------------------------------------------------
# Golden-fixture parity + prefix property (the DoD projection test, server side)
# ---------------------------------------------------------------------------


def _golden_events() -> list[dict]:
    root = Path(__file__).resolve()
    for parent in root.parents:
        candidate = (
            parent
            / "packages/service-contracts/src/copilot_service_contracts"
            / "work_ledger_golden_events.json"
        )
        if candidate.exists():
            return json.loads(candidate.read_text())["events"]
    raise AssertionError("work_ledger_golden_events.json not found")


class TestGoldenFixture:
    def test_fold_of_golden_events_matches_checked_in_expectation(self) -> None:
        # The full golden sequence resolves its one gate and terminals its one
        # (degenerate) stage → zero pending work. A stable, meaningful checked-in
        # expectation: a fully-worked run has an empty queue.
        assert PendingWorkFold.fold_raw(_golden_events()) == ()

    def test_every_event_prefix_matches_incremental_state(self) -> None:
        events = _golden_events()
        expected: dict[int, list[tuple[str, str]]] = {
            n: [] for n in range(len(events) + 1)
        }
        # gate_01 opens at seq 1 (prefix length 1) and resolves at seq 2 — the
        # single interval where the queue is non-empty.
        expected[1] = [("gate", "gate_01")]
        for n in range(len(events) + 1):
            assert _pending_keys(events[:n]) == expected[n], f"prefix {n}"


# ---------------------------------------------------------------------------
# fold() (typed-envelope path): created_at / run_id / conversation_id off the
# envelope, ledger_id via the A1 formatter.
# ---------------------------------------------------------------------------


@dataclass
class _Envelope:
    event_type: str
    sequence_no: int
    payload: dict
    created_at: datetime
    run_id: str
    conversation_id: str


class TestFoldTypedEnvelopes:
    def test_fold_reads_envelope_metadata(self) -> None:
        when = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
        envelope = _Envelope(
            event_type="gate.opened",
            sequence_no=1,
            payload=_gate_opened(1, gate_id="g1")["payload"],
            created_at=when,
            run_id=_RUN,
            conversation_id=_CONV,
        )
        items = PendingWorkFold.fold([envelope])
        assert len(items) == 1
        item = items[0]
        assert item.run_id == _RUN
        assert item.conversation_id == _CONV
        assert item.opened_at == when
        # A1 formatter over (run_id, seq) — not the fallback ``r…·…`` scheme.
        assert item.ledger_id.startswith("r") and "·" in item.ledger_id
