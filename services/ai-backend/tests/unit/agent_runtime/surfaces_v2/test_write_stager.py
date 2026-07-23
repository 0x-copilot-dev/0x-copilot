"""WriteStager behaviour tests (PRD-D1).

Drives the real :class:`WriteStager` over the real transport adapter
(``RuntimeStageLedger`` → ``RuntimeEventProducer`` → in-memory event store, so
the projector allow-list runs) plus an in-memory draft store. Asserts the
propose/edit/decision matrix cell-by-cell, that authorship spans are computed and
carried, that a stale base rev conflicts and emits nothing, and — the fail-closed
core — that NO path emits ``write.applied`` and the draft never becomes ``sent``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from agent_runtime.surfaces_v2.staging import (
    MalformedDecision,
    StageFrozen,
    StageNotFound,
    StagedWriteStatus,
    StaleRevision,
    UnsupportedDecision,
    WriteStager,
)
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_ORG = "org_acme"
_USER = "user_sarah"
_RUN = "run_launch"
_CONV = "conv_launch"


class StagerHarness:
    """Wires a stager over the in-memory store + draft store, and a seeded run."""

    def __init__(self) -> None:
        self.store = InMemoryRuntimeApiStore()
        self.drafts = InMemoryDraftStore()
        producer = RuntimeEventProducer(persistence=self.store, event_store=self.store)
        self.stager = WriteStager(
            draft_store=self.drafts,
            ledger=RuntimeStageLedger(event_producer=producer),
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

    async def seed_draft(
        self, content: str = "Dear team, launch is Friday."
    ) -> DraftRecord:
        draft_id = uuid4().hex
        record = DraftRecord(
            draft_id=draft_id,
            version=1,
            org_id=_ORG,
            conversation_id=_CONV,
            run_id=_RUN,
            user_id=_USER,
            title="Launch email",
            content_text=content,
            target_connector="gmail",
            status=DraftStatus.SEND_PENDING_APPROVAL,
        )
        return await self.drafts.insert_version(record)

    async def stage(self, draft: DraftRecord):
        return await self.stager.stage(
            run=self.run,
            org_id=_ORG,
            run_id=_RUN,
            draft=draft,
            target_connector="gmail",
            target_op="send",
        )

    def event_types(self) -> list[str]:
        return [self._type(event) for event in self.store.events_by_run.get(_RUN, [])]

    @staticmethod
    def _type(event: object) -> str:
        value = getattr(getattr(event, "event_type", None), "value", None)
        return (
            value if isinstance(value, str) else str(getattr(event, "event_type", ""))
        )


class TestStagePropose:
    async def test_stage_emits_surface_created_write_staged_revision_one(self) -> None:
        h = StagerHarness()
        draft = await h.seed_draft()
        state = await h.stage(draft)

        assert h.event_types() == ["surface.created", "write.staged", "revision.added"]
        assert state.status is StagedWriteStatus.STAGED
        assert state.latest_rev == 1
        assert state.revisions[0].author == "agent"
        assert state.revisions[0].authorship_spans == ()
        assert state.draft_id == draft.draft_id
        assert state.target_connector == "gmail"
        assert state.target_op == "send"
        # NOTHING executed: no write.applied, draft still pending (never sent).
        assert "write.applied" not in h.event_types()
        latest = await h.drafts.latest(org_id=_ORG, draft_id=draft.draft_id)
        assert latest.status is DraftStatus.SEND_PENDING_APPROVAL


class TestUserRevision:
    async def test_user_revision_bumps_rev_and_emits_spans(self) -> None:
        h = StagerHarness()
        draft = await h.seed_draft("Dear team, launch is Friday.")
        state = await h.stage(draft)

        new_body = "Dear team, launch is Monday."
        state = await h.stager.add_user_revision(
            run=h.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=state.stage_id,
            base_rev=1,
            content_text=new_body,
        )
        assert state.latest_rev == 2
        rev2 = state.revisions[1]
        assert rev2.author == "user"
        assert rev2.authorship_spans  # a diffed span exists
        marked = "".join(new_body[s.start : s.end] for s in rev2.authorship_spans)
        # Only the changed region is marked ("Fri"→"Mon"; the common "day" tail
        # and the unchanged "Dear team, launch is " prefix stay unmarked).
        assert "Mon" in marked
        assert "Dear team" not in marked
        # A new draft version holds the edited content.
        v2 = await h.drafts.get_version(org_id=_ORG, draft_id=draft.draft_id, version=2)
        assert v2.content_text == new_body
        assert "write.applied" not in h.event_types()

    async def test_stale_base_rev_conflicts_and_emits_nothing(self) -> None:
        h = StagerHarness()
        draft = await h.seed_draft()
        state = await h.stage(draft)
        before = len(h.event_types())
        with pytest.raises(StaleRevision):
            await h.stager.add_user_revision(
                run=h.run,
                org_id=_ORG,
                run_id=_RUN,
                stage_id=state.stage_id,
                base_rev=99,
                content_text="whatever",
            )
        assert len(h.event_types()) == before  # no event emitted on the 409

    async def test_edit_after_reject_is_frozen(self) -> None:
        h = StagerHarness()
        draft = await h.seed_draft()
        state = await h.stage(draft)
        await h.stager.record_decision(
            run=h.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=state.stage_id,
            decision="reject",
            rev=1,
        )
        with pytest.raises(StageFrozen):
            await h.stager.add_user_revision(
                run=h.run,
                org_id=_ORG,
                run_id=_RUN,
                stage_id=state.stage_id,
                base_rev=1,
                content_text="x",
            )


class TestDecisionMatrix:
    async def test_approve_latest_rev_pins_and_records(self) -> None:
        h = StagerHarness()
        draft = await h.seed_draft()
        state = await h.stage(draft)
        state = await h.stager.record_decision(
            run=h.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=state.stage_id,
            decision="approve",
            rev=1,
        )
        assert state.status is StagedWriteStatus.APPROVED
        assert state.approved_rev == 1
        assert "write.applied" not in h.event_types()

    async def test_approve_non_latest_rev_409_stale_no_event(self) -> None:
        h = StagerHarness()
        draft = await h.seed_draft()
        state = await h.stage(draft)
        await h.stager.add_user_revision(
            run=h.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=state.stage_id,
            base_rev=1,
            content_text="new body",
        )
        before = len(h.event_types())
        with pytest.raises(StaleRevision):
            await h.stager.record_decision(
                run=h.run,
                org_id=_ORG,
                run_id=_RUN,
                stage_id=state.stage_id,
                decision="approve",
                rev=1,  # rev 1, but latest is 2
            )
        assert len(h.event_types()) == before

    async def test_approve_missing_rev_is_malformed(self) -> None:
        h = StagerHarness()
        draft = await h.seed_draft()
        state = await h.stage(draft)
        with pytest.raises(MalformedDecision):
            await h.stager.record_decision(
                run=h.run,
                org_id=_ORG,
                run_id=_RUN,
                stage_id=state.stage_id,
                decision="approve",
                rev=None,
            )

    async def test_idempotent_reapprove_same_rev_no_duplicate_event(self) -> None:
        h = StagerHarness()
        draft = await h.seed_draft()
        state = await h.stage(draft)
        await h.stager.record_decision(
            run=h.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=state.stage_id,
            decision="approve",
            rev=1,
        )
        count_after_first = h.event_types().count("decision.recorded")
        state = await h.stager.record_decision(
            run=h.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=state.stage_id,
            decision="approve",
            rev=1,
        )
        assert state.status is StagedWriteStatus.APPROVED
        assert h.event_types().count("decision.recorded") == count_after_first

    async def test_second_decision_after_approve_is_frozen(self) -> None:
        h = StagerHarness()
        draft = await h.seed_draft()
        state = await h.stage(draft)
        await h.stager.record_decision(
            run=h.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=state.stage_id,
            decision="approve",
            rev=1,
        )
        with pytest.raises(StageFrozen):
            await h.stager.record_decision(
                run=h.run,
                org_id=_ORG,
                run_id=_RUN,
                stage_id=state.stage_id,
                decision="reject",
                rev=1,
            )

    async def test_reject_then_restore_repins_latest_rev(self) -> None:
        h = StagerHarness()
        draft = await h.seed_draft()
        state = await h.stage(draft)
        # Edit to rev 2 first so "re-pins latest" is observable.
        await h.stager.add_user_revision(
            run=h.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=state.stage_id,
            base_rev=1,
            content_text="edited body",
        )
        await h.stager.record_decision(
            run=h.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=state.stage_id,
            decision="reject",
            rev=2,
        )
        state = await h.stager.record_decision(
            run=h.run,
            org_id=_ORG,
            run_id=_RUN,
            stage_id=state.stage_id,
            decision="restore",
            rev=None,
        )
        assert state.status is StagedWriteStatus.STAGED
        restore = state.decisions[-1]
        assert restore.decision == "restore"
        assert restore.scope_rev == 2  # re-pinned to latest_rev
        assert "write.applied" not in h.event_types()

    async def test_restore_when_not_rejected_is_frozen(self) -> None:
        h = StagerHarness()
        draft = await h.seed_draft()
        state = await h.stage(draft)
        with pytest.raises(StageFrozen):
            await h.stager.record_decision(
                run=h.run,
                org_id=_ORG,
                run_id=_RUN,
                stage_id=state.stage_id,
                decision="restore",
                rev=None,
            )

    async def test_hold_is_unsupported_422(self) -> None:
        h = StagerHarness()
        draft = await h.seed_draft()
        state = await h.stage(draft)
        with pytest.raises(UnsupportedDecision):
            await h.stager.record_decision(
                run=h.run,
                org_id=_ORG,
                run_id=_RUN,
                stage_id=state.stage_id,
                decision="hold",
                rev=1,
            )

    async def test_unknown_stage_id_is_not_found(self) -> None:
        h = StagerHarness()
        await h.seed_draft()
        with pytest.raises(StageNotFound):
            await h.stager.record_decision(
                run=h.run,
                org_id=_ORG,
                run_id=_RUN,
                stage_id="ghost",
                decision="approve",
                rev=1,
            )
