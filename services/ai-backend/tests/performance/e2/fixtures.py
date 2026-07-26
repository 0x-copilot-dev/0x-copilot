"""Fixed, synthetic ledger fixtures for the E2 performance gate.

The fixtures are deliberately constructed from public wire shapes.  They do
not use a database, clock, model, or network; a future projection regression
therefore has one deterministic input and a reproducible operation budget.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from typing import Any

RUN_ID = "run_e2_performance_0123456789abcdef"
CONVERSATION_ID = "conv_e2_performance"
CREATED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()


class CountingEventSequence:
    """Sequence-like iterable which records source traversal count.

    The folds are allowed a small, explicit number of complete passes (for
    example ReceiptFold composes StagedWriteFold).  A nested re-scan becomes an
    obvious multiplication here, without using a timing assertion.
    """

    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events = events
        self.iterated = 0

    def __iter__(self) -> Iterator[dict[str, object]]:
        for event in self.events:
            self.iterated += 1
            yield event


def replay_events(count: int) -> list[dict[str, object]]:
    """Return exactly ``count`` coherent surface + stage ledger events.

    A four-event cycle gives every fold meaningful work: surface creation,
    view derivation, a staged write, and its revision.  IDs remain unique, so
    accidental per-surface scans and dictionary growth both show up at 10k.
    """

    if count <= 0:
        raise ValueError("count must be positive")
    events: list[dict[str, object]] = []
    for seq in range(1, count + 1):
        bucket = (seq - 1) // 4
        position = (seq - 1) % 4
        surface_id = f"surface_{bucket:05d}"
        stage_id = f"stage_{bucket:05d}"
        if position == 0:
            event_type = "surface.created"
            payload: dict[str, object] = {
                "v": 1,
                "surface_id": surface_id,
                "kind": "table",
                "source": {"connector": "local", "op": "list_rows"},
                "title": f"Rows {bucket}",
                "payload_ref": f"call:{bucket}",
            }
        elif position == 1:
            event_type = "view.derived"
            payload = {
                "v": 1,
                "surface_id": surface_id,
                "tier": "generic",
                "basis": "schema",
            }
        elif position == 2:
            event_type = "write.staged"
            payload = {
                "v": 1,
                "stage_id": stage_id,
                "surface_id": surface_id,
                "target": {"connector": "local", "op": "write_file"},
                "proposal_ref": f"draft://{stage_id}/v1",
            }
        else:
            event_type = "revision.added"
            payload = {
                "v": 1,
                "stage_id": stage_id,
                "rev": 1,
                "author": "agent",
                "proposal_ref": f"draft://{stage_id}/v1",
                "diff_ref": f"draft://{stage_id}/v1",
                "authorship_spans": [],
            }
        events.append(
            {
                "event_type": event_type,
                "sequence_no": seq,
                "created_at": CREATED_AT,
                "run_id": RUN_ID,
                "conversation_id": CONVERSATION_ID,
                "payload": payload,
            }
        )
    return events


def rowset_events(rows: int) -> list[dict[str, object]]:
    """One 10k-row staged CSV update, with no hidden per-row IO."""

    if rows <= 0:
        raise ValueError("rows must be positive")
    stage_id = "stage_csv_capacity"
    row_values: list[dict[str, object]] = []
    for index in range(rows):
        row_values.append(
            {
                "row_key": f"row_{index:05d}",
                "title": f"Customer {index}",
                "target_args": {"path": "exports/customers.csv", "row": index},
                "changes": [{"field": "status", "old": "lead", "new": "active"}],
            }
        )
    return [
        {
            "event_type": "write.staged",
            "sequence_no": 1,
            "created_at": CREATED_AT,
            "run_id": RUN_ID,
            "conversation_id": CONVERSATION_ID,
            "payload": {
                "v": 1,
                "stage_id": stage_id,
                "surface_id": "surface_csv_capacity",
                "target": {"connector": "local", "op": "write_csv"},
                "proposal_ref": f"stage://{stage_id}/v1",
                "rows": rows,
                "agent_holds": [],
            },
        },
        {
            "event_type": "revision.added",
            "sequence_no": 2,
            "created_at": CREATED_AT,
            "run_id": RUN_ID,
            "conversation_id": CONVERSATION_ID,
            "payload": {
                "v": 1,
                "stage_id": stage_id,
                "rev": 1,
                "author": "agent",
                "proposal_ref": f"stage://{stage_id}/v1",
                "diff_ref": f"stage://{stage_id}/v1",
                "rowset": {"rows": row_values},
            },
        },
    ]


def raw_json_shape(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """A type helper used by adapter-equivalence tests."""

    return [dict(event) for event in events]


def event_count(value: Mapping[str, Any]) -> int:
    """Read a report count without importing a production contract."""

    return int(value["event_count"])
