"""WriteStager.stage_rowset / record_row_decision tests (PRD-D3).

Drives the REAL stager over the real transport ledger (``RuntimeStageLedger`` →
``RuntimeEventProducer`` → in-memory event store, so the projector allow-list
runs) with a SPY commit queue that must stay empty on every non-apply path.
Asserts: staging emits ``surface.created{kind: table}`` + ``write.staged`` +
``revision.added`` (rev 1); caps / duplicate keys / holds ⊄ rows reject 422 with
NO event; row decisions toggle stance + emit a row scope and NEVER enqueue; an
agent pre-hold reason survives a user override; a rev-scoped hold still 422s.
"""

from __future__ import annotations

import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.surfaces_v2.rowset import (
    AgentHold,
    RowFieldChange,
    RowStance,
    StagedRow,
)
from agent_runtime.surfaces_v2.staging import (
    InvalidRowset,
    StageFrozen,
    UnknownRowKey,
    UnsupportedDecision,
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
    """Records every enqueue. On any non-apply path it MUST stay empty."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def enqueue_stage_commit(self, **kwargs) -> None:  # noqa: ANN003
        self.calls.append(kwargs)


class _FakePolicy:
    """Deterministic allow-always resolver (bypass configurable per connector)."""

    def __init__(self, *, bypass: bool = False) -> None:
        self._bypass = bypass

    def bypass_for(self, *, connector: str, op: str) -> bool:  # noqa: ARG002
        return self._bypass


def _rows(n: int, *, changes: int = 1) -> tuple[StagedRow, ...]:
    return tuple(
        StagedRow(
            row_key=f"row{i}",
            title=f"Issue {i}",
            target_args={"id": f"row{i}", "priority": 2},
            changes=tuple(
                RowFieldChange(field=f"f{j}", old=1, new=2) for j in range(changes)
            ),
        )
        for i in range(n)
    )


class Harness:
    def __init__(self, *, bypass: bool = False) -> None:
        self.store = InMemoryRuntimeApiStore()
        self.queue = _SpyQueue()
        producer = RuntimeEventProducer(persistence=self.store, event_store=self.store)
        self.stager = WriteStager(
            draft_store=None,  # type: ignore[arg-type] — rowsets never touch drafts
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
            title="Reprioritize issues",
        )

    def event_types(self) -> list[str]:
        return [
            getattr(getattr(e, "event_type", None), "value", None)
            for e in self.store.events_by_run.get(_RUN, [])
        ]


class TestStageRowset:
    async def test_emits_table_surface_staged_and_rev_one(self) -> None:
        h = Harness()
        state = await h.stage(_rows(8))
        types = h.event_types()
        assert types[:3] == ["surface.created", "write.staged", "revision.added"]
        # surface.created carries kind=table.
        surface_evt = h.store.events_by_run[_RUN][0]
        assert surface_evt.payload["kind"] == "table"
        assert state.is_rowset()
        assert state.row_counts.total == 8
        assert state.row_counts.will_apply == 8
        assert state.status.value == "staged"
        assert h.queue.calls == []  # staging never enqueues (ask-first)

    async def test_row_caps_rejected_422_no_event(self) -> None:
        h = Harness()
        with pytest.raises(InvalidRowset):
            await h.stage(_rows(201))
        assert h.event_types() == []
        assert h.queue.calls == []

    async def test_too_many_changes_per_row_rejected_422_no_event(self) -> None:
        h = Harness()
        with pytest.raises(InvalidRowset):
            await h.stage(_rows(1, changes=21))
        assert h.event_types() == []

    async def test_duplicate_row_keys_rejected_422_no_event(self) -> None:
        h = Harness()
        dup = (
            StagedRow(row_key="x", title="A", target_args={}),
            StagedRow(row_key="x", title="B", target_args={}),
        )
        with pytest.raises(InvalidRowset):
            await h.stage(dup)
        assert h.event_types() == []

    async def test_holds_must_reference_existing_rows(self) -> None:
        h = Harness()
        with pytest.raises(InvalidRowset):
            await h.stage(_rows(2), holds=(AgentHold(row_key="ghost", reason="no"),))
        assert h.event_types() == []

    async def test_pre_hold_marks_row_held_with_reason(self) -> None:
        h = Harness()
        state = await h.stage(
            _rows(3), holds=(AgentHold(row_key="row1", reason="recent reply"),)
        )
        row1 = next(r for r in state.rows if r.row_key == "row1")
        assert row1.stance is RowStance.HELD
        assert row1.agent_hold_reason == "recent reply"
        assert state.row_counts.held == 1
        assert state.row_counts.will_apply == 2


class TestRecordRowDecision:
    async def test_toggle_stance_emits_row_scope_and_never_enqueues(self) -> None:
        h = Harness()
        state = await h.stage(_rows(3))
        state = await h.stager.record_row_decision(
            run=h.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=state.stage_id,
            decision="hold",
            row_keys=["row0"],
        )
        row0 = next(r for r in state.rows if r.row_key == "row0")
        assert row0.stance is RowStance.HELD
        assert row0.decided_by == "user"
        # Adversarial: a stance toggle NEVER enqueues a commit.
        assert h.queue.calls == []
        assert "decision.recorded" in h.event_types()
        assert "write.applied" not in h.event_types()

    async def test_override_pre_held_row_keeps_reason_sticky(self) -> None:
        h = Harness()
        state = await h.stage(
            _rows(3), holds=(AgentHold(row_key="row1", reason="call yesterday"),)
        )
        state = await h.stager.record_row_decision(
            run=h.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=state.stage_id,
            decision="approve",
            row_keys=["row1"],
        )
        row1 = next(r for r in state.rows if r.row_key == "row1")
        assert row1.stance is RowStance.WILL_APPLY
        assert row1.agent_hold_reason == "call yesterday"  # STICKY after override
        assert h.queue.calls == []

    async def test_unknown_row_key_404_no_event(self) -> None:
        h = Harness()
        state = await h.stage(_rows(2))
        before = len(h.event_types())
        with pytest.raises(UnknownRowKey):
            await h.stager.record_row_decision(
                run=h.run,
                org_id=_ORG,
                run_id=_RUN,
                stage_id=state.stage_id,
                decision="hold",
                row_keys=["nope"],
            )
        assert len(h.event_types()) == before

    async def test_rev_scoped_hold_still_422(self) -> None:
        h = Harness()
        state = await h.stage(_rows(2))
        with pytest.raises(UnsupportedDecision):
            await h.stager.record_decision(
                run=h.run,
                org_id=_ORG,
                run_id=_RUN,
                stage_id=state.stage_id,
                decision="hold",
                rev=1,
            )

    async def test_rev_scoped_approve_on_rowset_422(self) -> None:
        h = Harness()
        state = await h.stage(_rows(2))
        with pytest.raises(UnsupportedDecision):
            await h.stager.record_decision(
                run=h.run,
                org_id=_ORG,
                run_id=_RUN,
                stage_id=state.stage_id,
                decision="approve",
                rev=1,
            )
        assert h.queue.calls == []  # never enqueues a single-artifact commit

    async def test_decision_after_frozen_409(self) -> None:
        h = Harness()
        state = await h.stage(_rows(2))
        await h.stager.apply_rows(
            run=h.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=state.stage_id,
            rev=1,
            row_keys=["row0", "row1"],
        )
        with pytest.raises(StageFrozen):
            await h.stager.record_row_decision(
                run=h.run,
                org_id=_ORG,
                run_id=_RUN,
                stage_id=state.stage_id,
                decision="hold",
                row_keys=["row0"],
            )
