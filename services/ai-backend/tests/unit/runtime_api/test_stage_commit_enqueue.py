"""PRD-D2 enqueue-site tests: a NEW approve enqueues EXACTLY one commit command.

Idempotent re-approve, reject, restore, and revision NEVER enqueue; ``commit_queue
is None`` records the decision and enqueues nothing (fail-open to no-commit).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_commit_queue import RuntimeStageCommitQueue
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from agent_runtime.surfaces_v2.staging import WriteStager
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord

pytestmark = pytest.mark.anyio

_ORG = "org_acme"
_USER = "user_sarah"
_RUN = "run_launch"
_CONV = "conv_launch"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _run() -> RunRecord:
    return RunRecord(
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


class Fixture:
    def __init__(self, *, wire_queue: bool = True) -> None:
        self.store = InMemoryRuntimeApiStore()
        self.drafts = InMemoryDraftStore()
        producer = RuntimeEventProducer(persistence=self.store, event_store=self.store)
        self.stager = WriteStager(
            draft_store=self.drafts,
            ledger=RuntimeStageLedger(event_producer=producer),
            commit_queue=(
                RuntimeStageCommitQueue(queue=self.store) if wire_queue else None
            ),
        )
        self.run = _run()
        self.store.runs[_RUN] = self.run
        self.store.events_by_run.setdefault(_RUN, [])
        self.draft_id = uuid4().hex

    async def stage(self):
        record = await self.drafts.insert_version(
            DraftRecord(
                draft_id=self.draft_id,
                version=1,
                org_id=_ORG,
                conversation_id=_CONV,
                run_id=_RUN,
                user_id=_USER,
                title="Launch email",
                content_text="Dear team, launch Friday.",
                target_connector="gmail",
                status=DraftStatus.SEND_PENDING_APPROVAL,
            )
        )
        return await self.stager.stage(
            run=self.run,
            org_id=_ORG,
            run_id=_RUN,
            draft=record,
            target_connector="gmail",
            target_op="send",
        )

    async def decide(self, *, stage_id: str, decision: str, rev: int | None):
        return await self.stager.record_decision(
            run=self.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=stage_id,
            decision=decision,
            rev=rev,
        )

    @property
    def commands(self):  # noqa: ANN201
        return self.store.stage_commit_commands


class TestApproveEnqueues:
    async def test_approve_enqueues_exactly_one_command_with_pin(self) -> None:
        fx = Fixture()
        state = await fx.stage()
        await fx.decide(stage_id=state.stage_id, decision="approve", rev=1)

        assert len(fx.commands) == 1
        cmd = fx.commands[0]
        assert cmd.stage_id == state.stage_id
        assert cmd.rev == 1
        assert cmd.run_id == _RUN
        assert cmd.org_id == _ORG
        assert cmd.user_id == _USER
        assert cmd.conversation_id == _CONV
        # decision_seq points at the actual decision.recorded event.
        assert isinstance(cmd.decision_seq, int) and cmd.decision_seq > 0

    async def test_idempotent_re_approve_enqueues_nothing(self) -> None:
        fx = Fixture()
        state = await fx.stage()
        await fx.decide(stage_id=state.stage_id, decision="approve", rev=1)
        # Re-approve the SAME rev — D1 records no second decision event, so no
        # second enqueue (end-to-end idempotency).
        await fx.decide(stage_id=state.stage_id, decision="approve", rev=1)
        assert len(fx.commands) == 1


class TestNonApproveNeverEnqueues:
    async def test_reject_never_enqueues(self) -> None:
        fx = Fixture()
        state = await fx.stage()
        await fx.decide(stage_id=state.stage_id, decision="reject", rev=1)
        assert fx.commands == []

    async def test_restore_never_enqueues(self) -> None:
        fx = Fixture()
        state = await fx.stage()
        await fx.decide(stage_id=state.stage_id, decision="reject", rev=1)
        await fx.decide(stage_id=state.stage_id, decision="restore", rev=1)
        assert fx.commands == []

    async def test_revision_never_enqueues(self) -> None:
        fx = Fixture()
        state = await fx.stage()
        await fx.stager.add_user_revision(
            run=fx.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=state.stage_id,
            base_rev=1,
            content_text="edited",
            title=None,
        )
        assert fx.commands == []


class TestNoQueueFailsOpenToNoCommit:
    async def test_none_commit_queue_records_decision_but_enqueues_nothing(
        self,
    ) -> None:
        fx = Fixture(wire_queue=False)
        state = await fx.stage()
        result = await fx.decide(stage_id=state.stage_id, decision="approve", rev=1)
        # The decision is recorded (fold shows APPROVED) but nothing is enqueued —
        # fail-open to no-commit, never to execution.
        assert result.status.value == "approved"
        assert fx.commands == []
