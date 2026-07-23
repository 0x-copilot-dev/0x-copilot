"""End-to-end tests for the PRD-D2 worker CommitEngine handler (``handlers/stage_commit.py``).

Drives the REAL enqueue → command → handler → CommitEngine path over in-memory
stores with a SPY connector as the only side-effecting boundary. Proves: the exact
approved body is dispatched (FR-C3), duplicate commands are inert, precondition
drift refuses + ledgers failed, the stale/ungated command no-ops without an event,
a commit flips the draft to SENT + audits, a failure leaves the draft pending, and
``write.applied`` carries rev + decided_by + receipt ref.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_commit_queue import RuntimeStageCommitQueue
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from agent_runtime.capabilities.surfaces.commit import ConnectorCommitResult
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from agent_runtime.surfaces_v2.commit_engine import (
    InMemoryStageCommitLedger,
    StageCommitConnectorError,
    StageCommitRequest,
)
from agent_runtime.surfaces_v2.staging import WriteStager
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord
from runtime_worker.handlers.stage_commit import RuntimeStageCommitHandler

pytestmark = pytest.mark.anyio

_ORG = "org_acme"
_USER = "user_sarah"
_RUN = "run_launch"
_CONV = "conv_launch"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _SpyConnector:
    """Records the exact request dispatched; performs NO real side effect."""

    def __init__(self, *, raise_on_execute: Exception | None = None) -> None:
        self._raise = raise_on_execute
        self.execute_calls: list[StageCommitRequest] = []

    async def read_remote_state(self, request: StageCommitRequest):  # noqa: ANN201
        return None

    async def execute(self, request: StageCommitRequest) -> ConnectorCommitResult:
        self.execute_calls.append(request)
        if self._raise is not None:
            raise self._raise
        return ConnectorCommitResult(status="sent", external_ref="ext-1")


class Harness:
    def __init__(self, *, connector: _SpyConnector | None = None) -> None:
        self.store = InMemoryRuntimeApiStore()
        self.drafts = InMemoryDraftStore()
        self.connector = connector or _SpyConnector()
        producer = RuntimeEventProducer(persistence=self.store, event_store=self.store)
        self.stager = WriteStager(
            draft_store=self.drafts,
            ledger=RuntimeStageLedger(event_producer=producer),
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
            draft_store=self.drafts,
            connector=self.connector,
            ledger=InMemoryStageCommitLedger(),
        )
        self.draft_id = uuid4().hex

    async def seed_draft(
        self, *, content: str = "Dear team, launch Friday."
    ) -> DraftRecord:
        return await self.drafts.insert_version(
            DraftRecord(
                draft_id=self.draft_id,
                version=1,
                org_id=_ORG,
                conversation_id=_CONV,
                run_id=_RUN,
                user_id=_USER,
                title="Launch email",
                content_text=content,
                target_connector="gmail",
                target_metadata={"to": "vip@acme.test"},
                status=DraftStatus.SEND_PENDING_APPROVAL,
            )
        )

    async def stage_edit_approve(self, *, edit: str | None = None):
        record = await self.drafts.latest(org_id=_ORG, draft_id=self.draft_id)
        state = await self.stager.stage(
            run=self.run,
            org_id=_ORG,
            run_id=_RUN,
            draft=record,
            target_connector="gmail",
            target_op="send",
        )
        if edit is not None:
            state = await self.stager.add_user_revision(
                run=self.run,
                org_id=_ORG,
                run_id=_RUN,
                stage_id=state.stage_id,
                base_rev=1,
                content_text=edit,
                title=None,
            )
        await self.stager.record_decision(
            run=self.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=state.stage_id,
            decision="approve",
            rev=state.latest_rev,
        )
        return state

    @property
    def command(self):  # noqa: ANN201
        return self.store.stage_commit_commands[0]

    def event_types(self) -> list[str]:
        return [
            getattr(getattr(e, "event_type", None), "value", None)
            for e in self.store.events_by_run.get(_RUN, [])
        ]

    def write_applied_events(self) -> list[object]:
        return [
            e
            for e in self.store.events_by_run.get(_RUN, [])
            if getattr(getattr(e, "event_type", None), "value", None) == "write.applied"
        ]

    async def any_draft_sent(self) -> bool:
        for (org, _d), versions in self.drafts.versions.items():
            if org == _ORG and any(v.status is DraftStatus.SENT for v in versions):
                return True
        return False


class TestCommittedFlow:
    async def test_committed_flow_dispatches_exact_approved_body(self) -> None:
        h = Harness()
        await h.seed_draft()
        await h.stage_edit_approve(edit="Dear team, launch MONDAY (edited).")

        await h.handler.handle(h.command)

        # Exactly one send; the connector received the EDITED (approved rev 2) body
        # byte-for-byte — what-you-approve-is-what-executes (FR-C3).
        assert len(h.connector.execute_calls) == 1
        args = h.connector.execute_calls[0].tool_arguments()
        assert args["body"] == "Dear team, launch MONDAY (edited)."

    async def test_committed_flips_draft_to_sent_and_writes_audit(self) -> None:
        h = Harness()
        await h.seed_draft()
        await h.stage_edit_approve()
        await h.handler.handle(h.command)

        assert await h.any_draft_sent() is True
        latest = await h.drafts.latest(org_id=_ORG, draft_id=h.draft_id)
        assert latest.status is DraftStatus.SENT
        actions = [event_type for event_type, _record in h.store.audit_log]
        assert "surface.commit.committed" in actions

    async def test_write_applied_carries_rev_decided_by_and_receipt_ref(self) -> None:
        h = Harness()
        await h.seed_draft()
        state = await h.stage_edit_approve(edit="edited body")
        await h.handler.handle(h.command)

        applied = h.write_applied_events()
        assert len(applied) == 1
        payload = applied[0].payload
        assert payload["result"] == "applied"
        assert payload["rev"] == 2  # the approved (edited) rev
        assert payload["decided_by"]["actor"] == "user"
        assert payload["decided_by"]["decision_seq"] == h.command.decision_seq
        assert payload["connector_receipt_ref"] == (
            f"commit://{state.stage_id}/{h.command.decision_seq}"
        )


class TestDuplicateInert:
    async def test_duplicate_command_is_inert(self) -> None:
        h = Harness()
        await h.seed_draft()
        await h.stage_edit_approve()

        await h.handler.handle(h.command)
        await h.handler.handle(h.command)  # redelivery

        # Exactly ONE connector call and ONE write.applied — the second handle is
        # inert (the folded stage is now APPLIED, so the approval gate refuses).
        assert len(h.connector.execute_calls) == 1
        assert len(h.write_applied_events()) == 1


class TestPreconditionDrift:
    async def test_draft_status_changed_since_staging_refuses_ledgered_failed(
        self,
    ) -> None:
        h = Harness()
        await h.seed_draft()
        await h.stage_edit_approve()

        # Out-of-band drift: the draft moves off SEND_PENDING_APPROVAL after staging.
        latest = await h.drafts.latest(org_id=_ORG, draft_id=h.draft_id)
        await h.drafts.insert_version(
            latest.model_copy(
                update={
                    "id": uuid4().hex,
                    "version": latest.version + 1,
                    "status": DraftStatus.DISCARDED,
                }
            )
        )

        await h.handler.handle(h.command)

        # No send; a write.applied{failed, precondition_drift}; a drift audit row.
        assert h.connector.execute_calls == []
        applied = h.write_applied_events()
        assert len(applied) == 1
        assert applied[0].payload["result"] == "failed"
        assert applied[0].payload["failure"]["code"] == "precondition_drift"
        actions = [event_type for event_type, _record in h.store.audit_log]
        assert "surface.commit.aborted_precondition_drift" in actions


class TestGateRefusal:
    async def test_stale_command_stage_not_approved_noops_without_event(self) -> None:
        h = Harness()
        await h.seed_draft()
        # Stage WITHOUT approving — no approve decision exists.
        record = await h.drafts.latest(org_id=_ORG, draft_id=h.draft_id)
        state = await h.stager.stage(
            run=h.run,
            org_id=_ORG,
            run_id=_RUN,
            draft=record,
            target_connector="gmail",
            target_op="send",
        )
        # Forge a command as if an approve had happened.
        from runtime_api.schemas import RuntimeStageCommitCommand

        forged = RuntimeStageCommitCommand(
            stage_id=state.stage_id,
            run_id=_RUN,
            org_id=_ORG,
            user_id=_USER,
            conversation_id=_CONV,
            rev=1,
            decision_seq=999,  # no such approve decision exists
        )
        await h.handler.handle(forged)

        # Fail-closed: no send, NO write.applied event at all.
        assert h.connector.execute_calls == []
        assert h.write_applied_events() == []

    async def test_wrong_decision_seq_is_gated_out(self) -> None:
        h = Harness()
        await h.seed_draft()
        await h.stage_edit_approve()
        # Tamper: same stage/rev but a decision_seq that does not match the approve.
        tampered = h.command.model_copy(
            update={"decision_seq": h.command.decision_seq + 5}
        )
        await h.handler.handle(tampered)
        assert h.connector.execute_calls == []
        assert h.write_applied_events() == []


class TestFailureRetryable:
    async def test_failed_leaves_draft_pending_so_fresh_approve_can_retry(self) -> None:
        connector = _SpyConnector(
            raise_on_execute=StageCommitConnectorError("auth revoked")
        )
        h = Harness(connector=connector)
        await h.seed_draft()
        await h.stage_edit_approve()

        await h.handler.handle(h.command)

        # Failed apply: write.applied{failed, connector_error}; draft still pending.
        applied = h.write_applied_events()
        assert len(applied) == 1
        assert applied[0].payload["result"] == "failed"
        assert applied[0].payload["failure"]["code"] == "connector_error"
        latest = await h.drafts.latest(org_id=_ORG, draft_id=h.draft_id)
        assert latest.status is DraftStatus.SEND_PENDING_APPROVAL
        assert await h.any_draft_sent() is False
        actions = [event_type for event_type, _record in h.store.audit_log]
        assert "surface.commit.failed" in actions
