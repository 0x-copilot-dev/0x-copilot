"""WriteStager.apply_rows + allow-always branch tests (PRD-D3 adversarial core, DoD).

The fail-closed guarantee: only the current will-apply set can be applied, the
apply enqueues EXACTLY that set, a mismatched set 409s with zero side effects,
duplicate applies are idempotent, and — under an allow-always policy — unflagged
rows auto-apply (``actor: policy``) while agent pre-holds STILL hold (FR-C8).
"""

from __future__ import annotations

import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.surfaces_v2.rowset import AgentHold, RowFieldChange, StagedRow
from agent_runtime.surfaces_v2.staging import (
    ApplySetMismatch,
    StagedWriteStatus,
    WriteStager,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord

pytestmark = pytest.mark.anyio

_ORG = "org_acme"
_USER = "user_sarah"
_RUN = "run_bulk"
_CONV = "conv_bulk"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _SpyQueue:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def enqueue_stage_commit(self, **kwargs) -> None:  # noqa: ANN003
        self.calls.append(kwargs)


class _FakePolicy:
    def __init__(self, *, bypass: bool) -> None:
        self._bypass = bypass

    def bypass_for(self, *, connector: str, op: str) -> bool:  # noqa: ARG002
        return self._bypass


def _rows(n: int) -> tuple[StagedRow, ...]:
    return tuple(
        StagedRow(
            row_key=f"row{i}",
            title=f"Issue {i}",
            target_args={"id": f"row{i}", "priority": 2},
            changes=(RowFieldChange(field="priority", old=1, new=2),),
        )
        for i in range(n)
    )


class Harness:
    def __init__(self, *, bypass: bool = False) -> None:
        self.store = InMemoryRuntimeApiStore()
        self.queue = _SpyQueue()
        producer = RuntimeEventProducer(persistence=self.store, event_store=self.store)
        self.stager = WriteStager(
            draft_store=None,  # type: ignore[arg-type]
            ledger=RuntimeStageLedger(event_producer=producer),
            commit_queue=self.queue,
            policy_resolver=_FakePolicy(bypass=bypass),
        )
        self.run = RunRecord(
            run_id=_RUN,
            conversation_id=_CONV,
            org_id=_ORG,
            user_id=_USER,
            user_message_id="msg_1",
            trace_id="trace_1",
            model_provider="openai",
            model_name="gpt-5.4-mini",
            status=AgentRunStatus.RUNNING,
            runtime_context=AgentRuntimeContext(
                user_id=_USER,
                org_id=_ORG,
                roles=["employee"],
                run_id=_RUN,
                trace_id="trace_1",
                model_profile={
                    "provider": "openai",
                    "model_name": "gpt-5.4-mini",
                    "max_input_tokens": 128000,
                    "timeout_seconds": 30,
                    "temperature": 0,
                    "supports_streaming": True,
                },
            ),
        )
        self.store.runs[_RUN] = self.run
        self.store.events_by_run.setdefault(_RUN, [])

    async def stage(self, rows, holds=()):  # noqa: ANN001
        return await self.stager.stage_rowset(
            run=self.run,
            org_id=_ORG,
            run_id=_RUN,
            target_connector="linear",
            target_op="update_issue",
            rows=rows,
            agent_holds=holds,
            title="Reprioritize",
        )

    async def apply(self, stage_id, rev, keys):  # noqa: ANN001
        return await self.stager.apply_rows(
            run=self.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=stage_id,
            rev=rev,
            row_keys=keys,
        )

    def decision_events(self) -> list[dict]:
        return [
            e.payload
            for e in self.store.events_by_run.get(_RUN, [])
            if getattr(getattr(e, "event_type", None), "value", None)
            == "decision.recorded"
        ]


class TestApply:
    async def test_apply_emits_apply_decision_then_enqueues_exact_set(self) -> None:
        h = Harness()
        state = await h.stage(_rows(3))
        state = await h.apply(state.stage_id, 1, ["row0", "row1", "row2"])
        assert state.status is StagedWriteStatus.APPLY_PENDING
        # Exactly one enqueue carrying exactly the approved set.
        assert len(h.queue.calls) == 1
        assert set(h.queue.calls[0]["row_keys"]) == {"row0", "row1", "row2"}
        # An apply-scoped approve decision was recorded (apply: true).
        applies = [d for d in h.decision_events() if d.get("apply") is True]
        assert len(applies) == 1
        assert applies[0]["actor"] == "user"

    async def test_apply_after_override_applies_seven_holds_one(self) -> None:
        # DoD live-style: stage 8, pre-hold 2, override 1, apply 7.
        h = Harness()
        state = await h.stage(
            _rows(8),
            holds=(
                AgentHold(row_key="row5", reason="recent reply"),
                AgentHold(row_key="row7", reason="call yesterday"),
            ),
        )
        assert state.row_counts.will_apply == 6
        # Override row5 → approved (row7 stays held).
        state = await h.stager.record_row_decision(
            run=h.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=state.stage_id,
            decision="approve",
            row_keys=["row5"],
        )
        assert state.row_counts.will_apply == 7
        expected = [r.row_key for r in state.rows if r.stance.value == "will_apply"]
        assert "row7" not in expected and "row5" in expected and len(expected) == 7
        state = await h.apply(state.stage_id, 1, expected)
        assert state.status is StagedWriteStatus.APPLY_PENDING
        assert set(h.queue.calls[-1]["row_keys"]) == set(expected)
        assert "row7" not in h.queue.calls[-1]["row_keys"]  # held row NEVER dispatched

    async def test_apply_set_mismatch_409_no_event_no_enqueue(self) -> None:
        h = Harness()
        state = await h.stage(_rows(3), holds=(AgentHold(row_key="row2", reason="x"),))
        before = len(h.store.events_by_run[_RUN])
        # will_apply is {row0,row1}; naming the held row2 mismatches.
        with pytest.raises(ApplySetMismatch):
            await h.apply(state.stage_id, 1, ["row0", "row1", "row2"])
        assert len(h.store.events_by_run[_RUN]) == before  # no event
        assert h.queue.calls == []  # no enqueue

    async def test_apply_subset_of_will_apply_is_mismatch(self) -> None:
        # WYSIWYG: you apply EXACTLY the will-apply set — a subset 409s.
        h = Harness()
        state = await h.stage(_rows(3))
        with pytest.raises(ApplySetMismatch):
            await h.apply(state.stage_id, 1, ["row0", "row1"])
        assert h.queue.calls == []

    async def test_duplicate_apply_idempotent_zero_additional_side_effects(
        self,
    ) -> None:
        h = Harness()
        state = await h.stage(_rows(2))
        await h.apply(state.stage_id, 1, ["row0", "row1"])
        events_after_first = len(h.store.events_by_run[_RUN])
        enqueues_after_first = len(h.queue.calls)
        # Re-apply the same rev+set while APPLY_PENDING — idempotent no-op.
        state = await h.apply(state.stage_id, 1, ["row0", "row1"])
        assert state.status is StagedWriteStatus.APPLY_PENDING
        assert len(h.store.events_by_run[_RUN]) == events_after_first
        assert len(h.queue.calls) == enqueues_after_first


class TestAllowAlways:
    async def test_auto_applies_unflagged_rows_actor_policy(self) -> None:
        h = Harness(bypass=True)
        state = await h.stage(_rows(3))
        # An apply-scoped approve with actor=policy fired at stage time (FR-C8).
        applies = [d for d in h.decision_events() if d.get("apply") is True]
        assert len(applies) == 1
        assert applies[0]["actor"] == "policy"
        assert set(applies[0]["scope"]["row_keys"]) == {"row0", "row1", "row2"}
        assert state.status is StagedWriteStatus.APPLY_PENDING
        assert len(h.queue.calls) == 1
        assert set(h.queue.calls[0]["row_keys"]) == {"row0", "row1", "row2"}

    async def test_never_dispatches_pre_held_rows(self) -> None:
        # DoD FR-C8: unflagged rows auto-apply; agent pre-holds STILL hold.
        h = Harness(bypass=True)
        state = await h.stage(
            _rows(3), holds=(AgentHold(row_key="row1", reason="risky"),)
        )
        applies = [d for d in h.decision_events() if d.get("apply") is True]
        assert len(applies) == 1
        assert set(applies[0]["scope"]["row_keys"]) == {"row0", "row2"}  # row1 excluded
        assert "row1" not in h.queue.calls[0]["row_keys"]
        row1 = next(r for r in state.rows if r.row_key == "row1")
        assert row1.stance.value == "held"
        assert row1.agent_hold_reason == "risky"

    async def test_ask_first_policy_never_auto_applies(self) -> None:
        h = Harness(bypass=False)
        state = await h.stage(_rows(3))
        assert [d for d in h.decision_events() if d.get("apply") is True] == []
        assert h.queue.calls == []
        assert state.status is StagedWriteStatus.STAGED
