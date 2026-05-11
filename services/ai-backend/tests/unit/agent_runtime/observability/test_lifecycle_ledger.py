"""Tests for :class:`LifecycleLedger` and :class:`LifecycleEventInspector`."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agent_runtime.observability.lifecycle_ledger import (
    LifecycleEventInspector,
    LifecycleKind,
    LifecycleLedger,
    OpenLifecycleEntry,
)


def _entry(
    kind: LifecycleKind = LifecycleKind.SUBAGENT,
    entity_id: str = "task_a",
    **overrides: object,
) -> OpenLifecycleEntry:
    defaults = {
        "kind": kind,
        "entity_id": entity_id,
        "parent_task_id": None,
        "subagent_id": None,
        "started_at": datetime(2026, 5, 11, tzinfo=timezone.utc),
        "payload_snapshot": {},
    }
    defaults.update(overrides)
    return OpenLifecycleEntry(**defaults)  # type: ignore[arg-type]


class TestLedgerOpenAndClose:
    async def test_open_then_close_round_trip(self) -> None:
        ledger = LifecycleLedger()
        await ledger.open(_entry(entity_id="task_1"))
        removed = await ledger.close(kind=LifecycleKind.SUBAGENT, entity_id="task_1")
        assert removed is not None
        assert removed.entity_id == "task_1"
        assert await ledger.open_count() == 0

    async def test_close_unknown_returns_none_and_does_not_raise(self) -> None:
        ledger = LifecycleLedger()
        assert (
            await ledger.close(kind=LifecycleKind.SUBAGENT, entity_id="missing") is None
        )

    async def test_open_replaces_previous_entry_with_same_key(self) -> None:
        ledger = LifecycleLedger()
        await ledger.open(_entry(entity_id="task_1", parent_task_id=None))
        await ledger.open(_entry(entity_id="task_1", parent_task_id="parent"))
        entries = await ledger.open_entries()
        assert len(entries) == 1
        assert entries[0].parent_task_id == "parent"

    async def test_open_entries_returns_in_insertion_order(self) -> None:
        ledger = LifecycleLedger()
        await ledger.open(_entry(entity_id="task_a"))
        await ledger.open(_entry(kind=LifecycleKind.TOOL_CALL, entity_id="call_b"))
        await ledger.open(_entry(entity_id="task_c"))
        entries = await ledger.open_entries()
        assert [e.entity_id for e in entries] == ["task_a", "call_b", "task_c"]


class TestLedgerConcurrentSafety:
    async def test_concurrent_open_and_close_do_not_deadlock(self) -> None:
        ledger = LifecycleLedger()

        async def open_close(i: int) -> None:
            await ledger.open(_entry(entity_id=f"task_{i}"))
            await asyncio.sleep(0)
            await ledger.close(kind=LifecycleKind.SUBAGENT, entity_id=f"task_{i}")

        await asyncio.gather(*(open_close(i) for i in range(20)))
        assert await ledger.open_count() == 0


class TestEventInspectorOpenOps:
    @pytest.mark.parametrize(
        "event_type, payload, expected_kind, expected_id",
        [
            (
                "subagent_started",
                {"task_id": "task_xyz"},
                LifecycleKind.SUBAGENT,
                "task_xyz",
            ),
            (
                "tool_call_started",
                {"call_id": "call_abc"},
                LifecycleKind.TOOL_CALL,
                "call_abc",
            ),
            (
                "model_call_started",
                {"message_id": "msg_123"},
                LifecycleKind.MODEL_CALL,
                "msg_123",
            ),
        ],
    )
    def test_recognized_started_events_yield_entries(
        self,
        event_type: str,
        payload: dict,
        expected_kind: LifecycleKind,
        expected_id: str,
    ) -> None:
        op = LifecycleEventInspector.open_op(
            event_type_value=event_type,
            payload=payload,
            parent_task_id="parent",
            subagent_id="sub",
        )
        assert op is not None
        assert op.kind is expected_kind
        assert op.entity_id == expected_id
        assert op.parent_task_id == "parent"
        assert op.subagent_id == "sub"

    def test_unrecognized_event_returns_none(self) -> None:
        assert (
            LifecycleEventInspector.open_op(
                event_type_value="run_started",
                payload={},
                parent_task_id=None,
                subagent_id=None,
            )
            is None
        )

    def test_started_event_without_id_field_returns_none(self) -> None:
        assert (
            LifecycleEventInspector.open_op(
                event_type_value="subagent_started",
                payload={},
                parent_task_id=None,
                subagent_id=None,
            )
            is None
        )

    def test_started_event_with_non_string_id_returns_none(self) -> None:
        assert (
            LifecycleEventInspector.open_op(
                event_type_value="subagent_started",
                payload={"task_id": 42},
                parent_task_id=None,
                subagent_id=None,
            )
            is None
        )


class TestEventInspectorCloseOps:
    @pytest.mark.parametrize(
        "event_type, payload, expected",
        [
            (
                "subagent_completed",
                {"task_id": "t1"},
                (LifecycleKind.SUBAGENT, "t1"),
            ),
            (
                "tool_call_completed",
                {"call_id": "c1"},
                (LifecycleKind.TOOL_CALL, "c1"),
            ),
            (
                "model_call_completed",
                {"message_id": "m1"},
                (LifecycleKind.MODEL_CALL, "m1"),
            ),
        ],
    )
    def test_recognized_completed_events(
        self, event_type: str, payload: dict, expected: tuple
    ) -> None:
        assert (
            LifecycleEventInspector.close_op(
                event_type_value=event_type, payload=payload
            )
            == expected
        )

    def test_unrecognized_completed_returns_none(self) -> None:
        assert (
            LifecycleEventInspector.close_op(event_type_value="run_failed", payload={})
            is None
        )
