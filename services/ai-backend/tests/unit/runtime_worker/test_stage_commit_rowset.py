"""Worker shared-effect-dispatch tests for bulk row-set apply (PRD-D3).

Drives the REAL enqueue → command → handler → per-row shared dispatch path over
in-memory stores with a SPY connector as the only side-effecting boundary.
Proves: only commanded rows reach the connector (held rows = zero traffic, byte-
equal row_args), per-row claims exist, a mid-apply failure yields ``partial`` +
``row_results``, an all-failed apply returns the stage to STAGED, a duplicate
command is inert, and a gate mismatch (wrong set / stale seq / non-pending) is a
no-op with NO ``write.applied`` event.
"""

from __future__ import annotations

import pytest
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_commit_queue import RuntimeStageCommitQueue
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from agent_runtime.capabilities.surfaces.commit import ConnectorCommitResult
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.surfaces_v2.commit_engine import (
    StageCommitConnectorError,
    StageCommitRequest,
)
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind
from agent_runtime.surfaces_v2.rowset import AgentHold, RowFieldChange, StagedRow
from agent_runtime.surfaces_v2.staging import (
    StagedWriteFold,
    StagedWriteStatus,
    WriteStager,
)
from runtime_adapters.in_memory.effect_claim_store import InMemoryEffectClaimStore
from tests.unit.rollout_testkit import legacy_staged_write_gate
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord, RuntimeStageCommitCommand
from runtime_worker.handlers.stage_commit import RuntimeStageCommitHandler
from runtime_worker.staged_write_effect_dispatch import (
    RuntimeStagedWriteEffectDispatcher,
)

pytestmark = pytest.mark.anyio

_ORG = "org_acme"
_USER = "user_sarah"
_RUN = "run_bulk"
_CONV = "conv_bulk"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _SpyConnector:
    """Records the exact per-row request dispatched; optional per-row failures."""

    def __init__(self, *, fail_keys: set[str] | None = None) -> None:
        self._fail = fail_keys or set()
        self.execute_calls: list[StageCommitRequest] = []

    async def read_remote_state(self, request: StageCommitRequest):
        return None

    async def execute(self, request: StageCommitRequest) -> ConnectorCommitResult:
        self.execute_calls.append(request)
        if request.row_key in self._fail:
            raise StageCommitConnectorError("row failed")
        return ConnectorCommitResult(
            status="sent", external_ref=f"ext-{request.row_key}"
        )

    async def authorize(self, request: StageCommitRequest) -> object:
        del request
        return object()


class _DispatcherFactory:
    def __init__(self, connector: _SpyConnector) -> None:
        self._connector = connector
        self._claims = InMemoryEffectClaimStore()

    def for_run(self, *, run: RunRecord) -> RuntimeStagedWriteEffectDispatcher:
        return RuntimeStagedWriteEffectDispatcher(
            scope=EffectExecutionScope(
                org_id=run.org_id,
                user_id=run.user_id,
                conversation_id=run.conversation_id,
                run_id=run.run_id,
                owner_ref=f"principal://users/{run.user_id}",
            ),
            claims=self._claims,
            connector=self._connector,  # type: ignore[arg-type]
        )


def _rows(n: int) -> tuple[StagedRow, ...]:
    return tuple(
        StagedRow(
            row_key=f"row{i}",
            title=f"Issue {i}",
            target_args={"id": f"row{i}", "priority": i + 2},
            changes=(RowFieldChange(field="priority", old=1, new=i + 2),),
        )
        for i in range(n)
    )


class Harness:
    def __init__(self, *, connector: _SpyConnector | None = None) -> None:
        self.store = InMemoryRuntimeApiStore()
        self.connector = connector or _SpyConnector()
        self.dispatcher_factory = _DispatcherFactory(self.connector)
        producer = RuntimeEventProducer(persistence=self.store, event_store=self.store)
        self.stager = WriteStager(
            draft_store=None,  # type: ignore[arg-type]
            ledger=RuntimeStageLedger(event_producer=producer),
            rollout_gate=legacy_staged_write_gate(),
            commit_queue=RuntimeStageCommitQueue(queue=self.store),
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
        self.handler = RuntimeStageCommitHandler(
            persistence=self.store,
            event_store=self.store,
            draft_store=None,
            dispatcher_factory=self.dispatcher_factory,
        )

    async def stage(self, rows, holds=()):
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

    async def apply(self, stage_id, rev, keys):
        return await self.stager.apply_rows(
            run=self.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=stage_id,
            rev=rev,
            row_keys=keys,
        )

    @property
    def command(self) -> RuntimeStageCommitCommand:
        return self.store.stage_commit_commands[-1]

    def write_applied_events(self) -> list[object]:
        return [
            e
            for e in self.store.events_by_run.get(_RUN, [])
            if getattr(getattr(e, "event_type", None), "value", None) == "write.applied"
        ]

    def fold(self):
        return StagedWriteFold.fold(self.store.events_by_run[_RUN])[
            self.command.stage_id
        ]


class TestRowsetDispatch:
    async def test_dispatches_only_commanded_rows_held_rows_zero_traffic(self) -> None:
        # DoD: stage 8, override the pre-held row, apply 7 → 7 applied + 1 untouched.
        h = Harness()
        state = await h.stage(
            _rows(8),
            holds=(AgentHold(row_key="row7", reason="call yesterday"),),
        )
        keys = [r.row_key for r in state.rows if r.stance.value == "will_apply"]
        assert keys == [f"row{i}" for i in range(7)]  # row7 held out
        await h.apply(state.stage_id, 1, keys)

        await h.handler.handle(h.command)

        # EXACTLY 7 rows dispatched; the held row never reached the connector.
        dispatched = {c.row_key for c in h.connector.execute_calls}
        assert dispatched == set(keys)
        assert "row7" not in dispatched
        assert len(h.connector.execute_calls) == 7
        # Row args sent byte-equal to the staged target_args (WYSIWYG / FR-C3).
        for call in h.connector.execute_calls:
            i = int(call.row_key.removeprefix("row"))
            assert call.tool_arguments() == {"id": f"row{i}", "priority": i + 2}

        applied = h.write_applied_events()
        assert len(applied) == 1
        payload = applied[0].payload
        assert payload["result"] == "applied"
        assert set(payload["row_keys"]) == set(keys)
        outcomes = {r["row_key"]: r["outcome"] for r in payload["row_results"]}
        assert outcomes == {k: "applied" for k in keys}

        # Ledger/receipt fold: exactly 7 applied, 1 held untouched.
        state = h.fold()
        assert state.status is StagedWriteStatus.APPLIED
        assert state.row_counts.applied == 7
        row7 = next(r for r in state.rows if r.row_key == "row7")
        assert row7.stance.value == "held"
        assert row7.apply_outcome is None  # untouched

    async def test_per_row_claim_written_before_side_effect(self) -> None:
        h = Harness()
        state = await h.stage(_rows(3))
        keys = ["row0", "row1", "row2"]
        await h.apply(state.stage_id, 1, keys)
        await h.handler.handle(h.command)
        # Each dispatched row left a committed shared-effect claim keyed by row.
        for k in keys:
            key = f"{h.command.stage_id}:1:{h.command.decision_seq}:{k}"
            claim = await h.dispatcher_factory._claims.get(
                org_id=_ORG,
                executor=EffectExecutorKind.MCP,
                idempotency_key=f"stage-commit:{key}",
            )
            assert claim is not None and claim.outcome.value == "applied"

    async def test_row_failure_mid_apply_yields_partial_and_row_results(self) -> None:
        h = Harness(connector=_SpyConnector(fail_keys={"row1"}))
        state = await h.stage(_rows(3))
        await h.apply(state.stage_id, 1, ["row0", "row1", "row2"])
        await h.handler.handle(h.command)

        payload = h.write_applied_events()[0].payload
        assert payload["result"] == "partial"
        outcomes = {r["row_key"]: r["outcome"] for r in payload["row_results"]}
        assert outcomes == {"row0": "applied", "row1": "failed", "row2": "applied"}
        state = h.fold()
        assert state.status is StagedWriteStatus.PARTIALLY_APPLIED
        assert state.row_counts.applied == 2
        assert state.row_counts.failed == 1

    async def test_retry_dispatches_only_failed_rows_and_reaches_applied(self) -> None:
        h = Harness(connector=_SpyConnector(fail_keys={"row1"}))
        state = await h.stage(_rows(3))
        await h.apply(state.stage_id, 1, ["row0", "row1", "row2"])
        await h.handler.handle(h.command)
        assert h.fold().status is StagedWriteStatus.PARTIALLY_APPLIED

        # The connector recovers. The fresh decision sequence creates fresh
        # idempotency keys, but only the failed row is eligible for dispatch.
        h.connector._fail.clear()
        await h.apply(state.stage_id, 1, ["row1"])
        retry_command = h.command
        assert retry_command.row_keys == ("row1",)
        await h.handler.handle(retry_command)

        assert [call.row_key for call in h.connector.execute_calls] == [
            "row0",
            "row1",
            "row2",
            "row1",
        ]
        folded = h.fold()
        assert folded.status is StagedWriteStatus.APPLIED
        assert folded.row_counts.applied == 3
        assert folded.row_counts.failed == 0
        assert [event.payload["result"] for event in h.write_applied_events()] == [
            "partial",
            "applied",
        ]

    async def test_failed_retry_stays_partial_and_never_resends_applied_rows(
        self,
    ) -> None:
        h = Harness(connector=_SpyConnector(fail_keys={"row1"}))
        state = await h.stage(_rows(2))
        await h.apply(state.stage_id, 1, ["row0", "row1"])
        await h.handler.handle(h.command)

        await h.apply(state.stage_id, 1, ["row1"])
        await h.handler.handle(h.command)

        assert [call.row_key for call in h.connector.execute_calls] == [
            "row0",
            "row1",
            "row1",
        ]
        folded = h.fold()
        assert folded.status is StagedWriteStatus.PARTIALLY_APPLIED
        assert folded.apply_result == "partial"
        assert folded.row_counts.applied == 1
        assert folded.row_counts.failed == 1

    async def test_all_rows_failed_yields_failed_and_stage_returns_to_staged(
        self,
    ) -> None:
        h = Harness(connector=_SpyConnector(fail_keys={"row0", "row1"}))
        state = await h.stage(_rows(2))
        await h.apply(state.stage_id, 1, ["row0", "row1"])
        await h.handler.handle(h.command)

        payload = h.write_applied_events()[0].payload
        assert payload["result"] == "failed"
        state = h.fold()
        # Apply consumed: back to STAGED (a fresh apply may retry).
        assert state.status is StagedWriteStatus.STAGED

    async def test_duplicate_command_is_inert(self) -> None:
        h = Harness()
        state = await h.stage(_rows(2))
        await h.apply(state.stage_id, 1, ["row0", "row1"])
        await h.handler.handle(h.command)
        await h.handler.handle(h.command)  # redelivery

        # Second handle is gated out (stage is APPLIED, not APPLY_PENDING).
        assert len(h.connector.execute_calls) == 2  # 2 rows, once each
        assert len(h.write_applied_events()) == 1


class TestRowsetGateRefusal:
    async def test_wrong_row_set_noops_without_event(self) -> None:
        h = Harness()
        state = await h.stage(_rows(3))
        await h.apply(state.stage_id, 1, ["row0", "row1", "row2"])
        tampered = h.command.model_copy(update={"row_keys": ("row0", "row1")})
        await h.handler.handle(tampered)
        assert h.connector.execute_calls == []
        assert h.write_applied_events() == []

    async def test_stale_decision_seq_noops_without_event(self) -> None:
        h = Harness()
        state = await h.stage(_rows(2))
        await h.apply(state.stage_id, 1, ["row0", "row1"])
        tampered = h.command.model_copy(
            update={"decision_seq": h.command.decision_seq + 7}
        )
        await h.handler.handle(tampered)
        assert h.connector.execute_calls == []
        assert h.write_applied_events() == []

    async def test_non_pending_stage_noops_without_event(self) -> None:
        # A command whose stage never reached APPLY_PENDING (no apply decision).
        h = Harness()
        state = await h.stage(_rows(2))
        forged = RuntimeStageCommitCommand(
            stage_id=state.stage_id,
            run_id=_RUN,
            org_id=_ORG,
            user_id=_USER,
            conversation_id=_CONV,
            rev=1,
            decision_seq=999,
            row_keys=("row0", "row1"),
        )
        await h.handler.handle(forged)
        assert h.connector.execute_calls == []
        assert h.write_applied_events() == []
