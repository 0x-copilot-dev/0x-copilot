"""Pure ReceiptFold tests (PRD-E1).

The receipt is a deterministic, total fold of a run's ledger — never
hand-assembled state (SDR §10 item 6). These tests drive the fold with raw
event dicts (the shape the store returns) and pin: the golden events fold to the
shared expected-receipt fixture (the referee the ts fold must reproduce); the
tiles equal an INDEPENDENT naive counter and refolding is byte-identical (the
DoD property test); two decision paths yield two different, correct receipts (the
DoD session-accuracy test); and the adversarial edges (malformed events,
policy auto-apply, partial-apply holds, raw "no view fit", codec ids).
"""

from __future__ import annotations

import random

from copilot_service_contracts.work_ledger import (
    load_ledger_expected_receipt,
    load_ledger_golden_events,
)

from agent_runtime.surfaces_v2.ledger_ids import LedgerIdCodec
from agent_runtime.surfaces_v2.receipt import ReceiptFold

RUN_ID = "run00000001abcdef"


# ---------------------------------------------------------------------------
# Raw-event builders
# ---------------------------------------------------------------------------


def _read(
    seq: int, *, connector="linear", op="get_issue", payload_ref="call:c1"
) -> dict:
    return {
        "event_type": "read.executed",
        "sequence_no": seq,
        "created_at": f"2026-01-01T00:00:{seq:02d}Z",
        "payload": {
            "v": 1,
            "call_id": "c1",
            "connector": connector,
            "op": op,
            "latency_ms": 12,
            "payload_ref": payload_ref,
        },
    }


def _surface(
    seq: int, *, surface="surf_1", kind="record", title="ENG-1", payload_ref="p/surf"
) -> dict:
    return {
        "event_type": "surface.created",
        "sequence_no": seq,
        "created_at": f"2026-01-01T00:00:{seq:02d}Z",
        "payload": {
            "v": 1,
            "surface_id": surface,
            "kind": kind,
            "source": {"connector": "linear", "op": "get_issue"},
            "title": title,
            "payload_ref": payload_ref,
        },
    }


def _view_derived(seq: int, *, surface="surf_1", tier="generic") -> dict:
    return {
        "event_type": "view.derived",
        "sequence_no": seq,
        "created_at": f"2026-01-01T00:00:{seq:02d}Z",
        "payload": {"v": 1, "surface_id": surface, "tier": tier, "basis": "schema"},
    }


def _staged(seq: int, *, stage="stage_1", surface="surf_1", rows=None) -> dict:
    payload: dict = {
        "v": 1,
        "stage_id": stage,
        "surface_id": surface,
        "target": {"connector": "linear", "op": "update_issue"},
        "proposal_ref": "draft://abcdef0123456789abcdef0123456789/v1",
    }
    if rows is not None:
        payload["rows"] = rows
    return {
        "event_type": "write.staged",
        "sequence_no": seq,
        "created_at": f"2026-01-01T00:00:{seq:02d}Z",
        "payload": payload,
    }


def _decision(
    seq: int, decision: str, *, stage="stage_1", actor="user", rev=1, row_keys=None
) -> dict:
    scope = {"row_keys": list(row_keys)} if row_keys is not None else {"rev": rev}
    return {
        "event_type": "decision.recorded",
        "sequence_no": seq,
        "created_at": f"2026-01-01T00:00:{seq:02d}Z",
        "payload": {
            "v": 1,
            "stage_id": stage,
            "decision": decision,
            "scope": scope,
            "actor": actor,
        },
    }


def _applied(
    seq: int, *, stage="stage_1", rev=1, result="applied", row_keys=None
) -> dict:
    payload: dict = {"v": 1, "stage_id": stage, "rev": rev, "result": result}
    if row_keys is not None:
        payload["row_keys"] = list(row_keys)
    return {
        "event_type": "write.applied",
        "sequence_no": seq,
        "created_at": f"2026-01-01T00:00:{seq:02d}Z",
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# Independent naive tile counter (property-test referee — no fold internals)
# ---------------------------------------------------------------------------


def _naive_tiles(events: list[dict]) -> tuple[int, int, int, int]:
    reads = sum(1 for e in events if e["event_type"] == "read.executed")
    proposed = 0
    approved = 0
    stage_rows: dict[str, int] = {}
    stage_applied: dict[str, int] = {}
    stage_has_apply: dict[str, bool] = {}
    stage_rejected: dict[str, bool] = {}
    for e in events:
        et = e["event_type"]
        p = e["payload"]
        if et == "write.staged":
            sid = p["stage_id"]
            rows = p.get("rows")
            rows = (
                rows
                if isinstance(rows, int) and not isinstance(rows, bool) and rows >= 0
                else 1
            )
            stage_rows[sid] = rows
            proposed += rows
        elif et == "write.applied":
            sid = p.get("stage_id")
            stage_has_apply[sid] = True
            if p.get("result") in ("applied", "partial"):
                rk = p.get("row_keys")
                n = len(rk) if isinstance(rk, (list, tuple)) else 1
                approved += n
                stage_applied[sid] = stage_applied.get(sid, 0) + n
        elif et == "decision.recorded" and p.get("decision") == "reject":
            stage_rejected[p.get("stage_id")] = True
    held = 0
    for sid, rows in stage_rows.items():
        if stage_has_apply.get(sid):
            held += max(0, rows - stage_applied.get(sid, 0))
        elif stage_rejected.get(sid):
            held += rows
    return (reads, proposed, approved, held)


# ---------------------------------------------------------------------------
# DoD: fold == independent fold of the golden ledger
# ---------------------------------------------------------------------------


def test_golden_events_fold_matches_expected_receipt() -> None:
    fixture = load_ledger_golden_events()
    receipt = ReceiptFold.fold_raw(run_id=fixture["run_id"], events=fixture["events"])
    assert receipt.model_dump(mode="json") == load_ledger_expected_receipt()


def test_fold_is_independent_of_hand_state() -> None:
    """DoD property test: tiles equal an independent naive count; shuffled input
    re-sorts to the same output; refold is byte-identical (no hand-assembled
    accumulator, no wall-clock)."""

    fixture = load_ledger_golden_events()
    events = fixture["events"]
    run_id = fixture["run_id"]

    receipt = ReceiptFold.fold_raw(run_id=run_id, events=events)
    tiles = receipt.tiles
    assert (
        tiles.reads_auto_ran,
        tiles.writes_proposed,
        tiles.writes_approved,
        tiles.holds_untouched,
    ) == _naive_tiles(events)

    # Shuffled input is re-sorted by sequence_no before folding ⇒ identical.
    shuffled = list(events)
    random.Random(1234).shuffle(shuffled)
    shuffled_receipt = ReceiptFold.fold_raw(run_id=run_id, events=shuffled)
    assert shuffled_receipt.model_dump(mode="json") == receipt.model_dump(mode="json")

    # Refold is byte-identical, including a deterministic (non-wall-clock)
    # generated_at pinned to the highest-sequence folded event.
    refold = ReceiptFold.fold_raw(run_id=run_id, events=events)
    assert refold.model_dump(mode="json") == receipt.model_dump(mode="json")
    assert refold.generated_at == events[-1]["created_at"]


# ---------------------------------------------------------------------------
# DoD: session accuracy — two decision paths, two different receipts
# ---------------------------------------------------------------------------


def test_two_decision_paths_two_receipts() -> None:
    """Identical staged events; scenario A user-approves→applied, scenario B
    rejects. The two receipts differ in tiles AND row attribution."""

    prefix = [
        _read(1),
        _surface(2),
        _view_derived(3),
        _staged(4, rows=2),
    ]
    scenario_a = [
        *prefix,
        _decision(5, "approve", actor="user"),
        _applied(6, result="applied", row_keys=["a", "b"]),
    ]
    scenario_b = [
        *prefix,
        _decision(5, "reject", actor="user"),
    ]

    receipt_a = ReceiptFold.fold_raw(run_id=RUN_ID, events=scenario_a)
    receipt_b = ReceiptFold.fold_raw(run_id=RUN_ID, events=scenario_b)

    # Tiles differ: A applied 2 rows, held 0; B held all 2, approved 0.
    assert receipt_a.tiles.writes_approved == 2
    assert receipt_a.tiles.holds_untouched == 0
    assert receipt_b.tiles.writes_approved == 0
    assert receipt_b.tiles.holds_untouched == 2

    a_attribs = [row.attribution.value for row in receipt_a.rows]
    b_attribs = [row.attribution.value for row in receipt_b.rows]
    assert "approved" in a_attribs
    assert "rejected" not in a_attribs
    assert "rejected" in b_attribs
    assert "held" in b_attribs
    assert "approved" not in b_attribs
    # And the two receipts are genuinely distinct.
    assert receipt_a.model_dump(mode="json") != receipt_b.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Adversarial edges
# ---------------------------------------------------------------------------


def test_unknown_and_malformed_events_skipped_without_error() -> None:
    events = [
        _read(1),
        {
            "event_type": "usage.recorded",
            "sequence_no": 2,
            "created_at": "x",
            "payload": {},
        },
        {
            "event_type": "totally.unknown",
            "sequence_no": 3,
            "created_at": "y",
            "payload": None,
        },
        {
            "event_type": "write.staged",
            "sequence_no": 4,
            "created_at": "z",
            "payload": "not-a-dict",
        },
        {"event_type": "read.executed", "sequence_no": 5},  # missing payload/created_at
    ]
    receipt = ReceiptFold.fold_raw(run_id=RUN_ID, events=events)
    # Both reads count; the malformed staged/unknown events are ignored.
    assert receipt.tiles.reads_auto_ran == 2
    assert receipt.tiles.writes_proposed == 0


def test_policy_actor_apply_rows_are_auto_applied() -> None:
    events = [
        _staged(1, rows=1),
        _decision(2, "approve", actor="policy"),
        _applied(3, result="applied", row_keys=["a"]),
    ]
    receipt = ReceiptFold.fold_raw(run_id=RUN_ID, events=events)
    apply_rows = [r for r in receipt.rows if r.event_type.value == "write.applied"]
    assert len(apply_rows) == 1
    assert apply_rows[0].attribution.value == "auto_applied"


def test_partial_apply_yields_held_remainder_row() -> None:
    events = [
        _staged(1, rows=3),
        _decision(2, "approve", actor="user"),
        _applied(3, result="partial", row_keys=["a"]),
    ]
    receipt = ReceiptFold.fold_raw(run_id=RUN_ID, events=events)
    assert receipt.tiles.holds_untouched == 2
    held = [r for r in receipt.rows if r.attribution.value == "held"]
    assert len(held) == 1
    assert held[0].title == "2 rows held, untouched"


def test_raw_view_yields_no_view_fit_row() -> None:
    events = [
        _read(1),
        _surface(
            2, surface="surf_raw", kind="raw", title="raw blob", payload_ref="p/raw"
        ),
    ]
    receipt = ReceiptFold.fold_raw(run_id=RUN_ID, events=events)
    no_fit = [r for r in receipt.rows if r.attribution.value == "no_view_fit"]
    assert len(no_fit) == 1
    assert no_fit[0].title == "raw blob"


def test_row_ledger_ids_use_codec() -> None:
    events = [_read(7)]
    receipt = ReceiptFold.fold_raw(run_id=RUN_ID, events=events)
    assert receipt.rows[0].ledger_id == LedgerIdCodec.format(RUN_ID, 7)


def test_empty_events_fold_to_zero_receipt() -> None:
    receipt = ReceiptFold.fold_raw(run_id=RUN_ID, events=[])
    assert receipt.rows == ()
    assert receipt.tiles.reads_auto_ran == 0
    assert receipt.surface_id == f"receipt://{RUN_ID}"
    assert receipt.fold_ref == f"ledger://{RUN_ID}@0"
