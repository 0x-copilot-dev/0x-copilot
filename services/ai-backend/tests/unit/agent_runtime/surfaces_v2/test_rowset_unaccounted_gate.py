"""The account is re-established when a row is DECIDED, not only when it is staged.

``RowsetValidator`` runs at staging time. Everything after that reads the fold,
and the fold rebuilds each row from an untrusted ledger payload where
``RowsetValidator.sends_of`` is all-or-nothing: a ``revision.added`` whose rows
carry no ``sends`` key — a stage proposed before the accounting contract
existed, or a payload that lost it in transit — comes back with an EMPTY
account. Nothing downstream used to object, so such a row stayed approvable and
dispatchable while disclosing nothing at all.

These tests strip ``sends`` off the ledger AFTER a legitimate stage, which is
exactly that shape, and pin the two stager-side answers:

* an unaccounted row cannot be APPROVED and cannot be APPLIED — it raises
  :class:`RowsetUnaccounted`, and it takes its whole batch with it rather than
  quietly dropping out of one;
* holding it is still allowed, because a hold only ever removes rows from the
  outbound set — and once held, its accounted siblings apply normally.
"""

from __future__ import annotations

import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.surfaces_v2.rowset import (
    ProposedRow,
    RowFieldChange,
    StagedRow,
    StagedRowAccounting,
)
from agent_runtime.surfaces_v2.staging import (
    RowsetUnaccounted,
    StagedWriteStatus,
    WriteStager,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord
from tests.unit.rollout_testkit import legacy_staged_write_gate

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


class UnaccountedLedgerMixin:
    """Stage honestly, then deform the LEDGER — never the stager's input."""

    def build(self) -> None:
        self.store = InMemoryRuntimeApiStore()
        self.queue = _SpyQueue()
        producer = RuntimeEventProducer(persistence=self.store, event_store=self.store)
        self.stager = WriteStager(
            draft_store=None,  # type: ignore[arg-type]
            ledger=RuntimeStageLedger(event_producer=producer),
            rollout_gate=legacy_staged_write_gate(),
            commit_queue=self.queue,
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

    def rows(self, count: int) -> tuple[StagedRow, ...]:
        return tuple(
            StagedRowAccounting.for_proposed(
                ProposedRow(
                    row_key=f"row{index}",
                    title=f"Issue {index}",
                    target_args={"id": f"row{index}", "priority": 2},
                    changes=(RowFieldChange(field="priority", old=1, new=2),),
                )
            )
            for index in range(count)
        )

    async def stage(self, count: int = 3):  # noqa: ANN201
        return await self.stager.stage_rowset(
            run=self.run,
            org_id=_ORG,
            run_id=_RUN,
            target_connector="linear",
            target_op="update_issue",
            rows=self.rows(count),
            agent_holds=(),
            title="Reprioritize",
        )

    def strip_sends(self, *row_keys: str) -> None:
        """Remove the account from the persisted rows — the pre-contract shape."""

        for event in self.store.events_by_run[_RUN]:
            rowset = event.payload.get("rowset")
            if not isinstance(rowset, dict):
                continue
            for row in rowset.get("rows", []):
                if row.get("row_key") in row_keys:
                    row.pop("sends", None)

    def event_count(self) -> int:
        return len(self.store.events_by_run[_RUN])

    async def decide(self, stage_id: str, decision: str, keys: list[str]):  # noqa: ANN201
        return await self.stager.record_row_decision(
            run=self.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=stage_id,
            decision=decision,
            row_keys=keys,
        )

    async def apply(self, stage_id: str, rev: int, keys: list[str]):  # noqa: ANN201
        return await self.stager.apply_rows(
            run=self.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=stage_id,
            rev=rev,
            row_keys=keys,
        )


class TestAnUnaccountedRowIsNotApprovable(UnaccountedLedgerMixin):
    async def test_approving_it_raises_with_no_event(self) -> None:
        self.build()
        state = await self.stage(3)
        self.strip_sends("row1")
        before = self.event_count()

        with pytest.raises(RowsetUnaccounted):
            await self.decide(state.stage_id, "approve", ["row1"])

        assert self.event_count() == before  # the ledger records only what happened
        assert self.queue.calls == []

    async def test_the_message_names_the_row_and_the_rule(self) -> None:
        self.build()
        state = await self.stage(2)
        self.strip_sends("row1")

        with pytest.raises(RowsetUnaccounted) as caught:
            await self.decide(state.stage_id, "approve", ["row1"])

        message = caught.value.safe_message
        assert 'Row "row1"' in message
        assert "does not disclose every field it would send" in message
        assert message.endswith("Nothing was sent.")
        # The typed code is stable for the client; the text is the human half.
        assert caught.value.code == "rowset_unaccounted"

    async def test_one_bad_row_refuses_the_whole_approve(self) -> None:
        # Not "approve the two that are fine". The user pressed one button over
        # a named set; shrinking the set silently makes it a different action.
        self.build()
        state = await self.stage(3)
        self.strip_sends("row2")
        before = self.event_count()

        with pytest.raises(RowsetUnaccounted):
            await self.decide(state.stage_id, "approve", ["row0", "row1", "row2"])

        assert self.event_count() == before

    async def test_a_tampered_account_is_refused_too(self) -> None:
        # Not only an ABSENT account: one that no longer matches the args it
        # claims to describe fails the same gate, with its own reason.
        self.build()
        state = await self.stage(2)
        for event in self.store.events_by_run[_RUN]:
            rowset = event.payload.get("rowset")
            if not isinstance(rowset, dict):
                continue
            for row in rowset.get("rows", []):
                if row.get("row_key") == "row0":
                    row["sends"][1]["new"] = 99  # shown 99, sends 2

        with pytest.raises(RowsetUnaccounted) as caught:
            await self.decide(state.stage_id, "approve", ["row0"])

        assert "different from the one it discloses" in caught.value.safe_message


class TestAnUnaccountedRowIsNotAppliable(UnaccountedLedgerMixin):
    async def test_apply_raises_with_no_event_and_no_enqueue(self) -> None:
        # The residual in one test: the approval already sits in the ledger
        # (every row folds will_apply by default), so without this gate the
        # apply would enqueue a dispatch for a row disclosing nothing.
        self.build()
        state = await self.stage(3)
        self.strip_sends("row1")
        before = self.event_count()

        with pytest.raises(RowsetUnaccounted) as caught:
            await self.apply(state.stage_id, 1, ["row0", "row1", "row2"])

        assert 'Row "row1"' in caught.value.safe_message
        assert self.event_count() == before  # no apply decision was written
        assert self.queue.calls == []  # nothing was enqueued

    async def test_holding_the_row_is_the_way_forward(self) -> None:
        # A hold only ever REMOVES a row from the outbound set, so it stays
        # available on an unaccounted row — and once held, the accounted
        # siblings apply exactly as before.
        self.build()
        state = await self.stage(3)
        self.strip_sends("row1")

        state = await self.decide(state.stage_id, "hold", ["row1"])
        assert state.row_counts.held == 1

        state = await self.apply(state.stage_id, 1, ["row0", "row2"])

        assert state.status is StagedWriteStatus.APPLY_PENDING
        assert set(self.queue.calls[-1]["row_keys"]) == {"row0", "row2"}

    async def test_an_accounted_stage_still_applies(self) -> None:
        # The baseline the gate must not cost: nothing stripped, nothing refused.
        self.build()
        state = await self.stage(2)

        state = await self.apply(state.stage_id, 1, ["row0", "row1"])

        assert state.status is StagedWriteStatus.APPLY_PENDING
        assert len(self.queue.calls) == 1


class TestRowSendPlanHasNoThirdState(UnaccountedLedgerMixin):
    """Rows or a refusal — a caller can never iterate a shortened batch.

    ``row_send_plan`` is the single definition all three call sites use, so its
    two failure arms are pinned here rather than three times over.
    """

    async def test_it_returns_every_named_row_in_the_order_named(self) -> None:
        self.build()
        state = await self.stage(3)

        plan = state.row_send_plan(["row2", "row0"])

        assert plan.refusal is None
        assert [row.row_key for row in plan.rows] == ["row2", "row0"]

    async def test_a_key_with_no_staged_content_refuses_with_empty_rows(self) -> None:
        # The defensive arm: the fold keeps ``rows`` and ``staged_rows`` in step,
        # so this is unreachable through the ledger — but "no content" must mean
        # REFUSE rather than an absent row that silently leaves the batch.
        self.build()
        state = await self.stage(2)

        plan = state.row_send_plan(["row0", "ghost"])

        assert plan.rows == ()
        assert plan.refusal is not None
        assert plan.refusal.row_key == "ghost"
        assert "no staged content left" in plan.refusal.message()

    async def test_an_unaccounted_key_refuses_with_empty_rows(self) -> None:
        self.build()
        state = await self.stage(2)
        self.strip_sends("row1")
        state = await self.stager.get_state(  # re-fold after the deformation
            org_id=_ORG, run_id=_RUN, stage_id=state.stage_id
        )

        plan = state.row_send_plan(["row0", "row1"])

        assert plan.rows == ()
        assert plan.refusal is not None and plan.refusal.row_key == "row1"
