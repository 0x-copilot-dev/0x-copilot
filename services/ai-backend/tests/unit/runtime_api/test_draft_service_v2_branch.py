"""PRD-D1 propose-seam branch: ``DraftService.send`` v1↔v2 (flag-off DoD).

Pins the byte-identical guarantee at the propose seam:

* flag off (or unset) ⇒ the v1 path exactly — an ``ApprovalRequestRecord`` with
  ``kind="draft_send"`` is persisted, an ``approval_requested`` event fires, no
  ``stage_id`` is returned, and NO v2 ledger event (``write.staged`` /
  ``revision.added``) is emitted;
* flag on + a wired ``WriteStager`` ⇒ the v2 path — no approval row, no
  ``approval_requested`` event, ``write.staged`` + ``revision.added`` (rev 1,
  author agent) emitted, and ``stage_id`` surfaced on the response;
* the stager unwired (``None``) ⇒ the v1 path regardless of the flag (degrade-open,
  mirroring ``event_producer is None``).

Nothing executes in any branch: the draft never becomes ``sent`` and no
``write.applied`` event is ever emitted.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from agent_runtime.api.draft_service import DraftService
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from agent_runtime.capabilities.auth_gate import (
    CapabilityAuthCheck,
    CapabilityAuthOutcome,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from agent_runtime.surfaces_v2.staging import WriteStager
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, DraftSendRequest, RunRecord

pytestmark = pytest.mark.anyio

_ORG = "org_acme"
_USER = "user_sarah"
_RUN = "run_existing"
_CONV = "conv_1"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _StubAuthGate:
    async def check(
        self, *, target_connector: str, runtime_context: object
    ) -> CapabilityAuthCheck:
        return CapabilityAuthCheck(outcome=CapabilityAuthOutcome.AUTHENTICATED)


class Harness:
    def __init__(self, *, wire_stager: bool) -> None:
        self.store = InMemoryRuntimeApiStore()
        self.drafts = InMemoryDraftStore()
        self.producer = RuntimeEventProducer(
            persistence=self.store, event_store=self.store
        )
        run = RunRecord(
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
        self.store.runs[_RUN] = run
        self.store.events_by_run.setdefault(_RUN, [])
        stager = (
            WriteStager(
                draft_store=self.drafts,
                ledger=RuntimeStageLedger(event_producer=self.producer),
            )
            if wire_stager
            else None
        )
        self.service = DraftService(
            store=self.drafts,
            persistence=self.store,
            auth_gate=_StubAuthGate(),
            event_producer=self.producer,
            write_stager=stager,
        )
        self.draft_id = uuid4().hex

    async def seed_draft(self) -> None:
        await self.drafts.insert_version(
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

    async def send(self):
        return await self.service.send(
            org_id=_ORG,
            user_id=_USER,
            draft_id=self.draft_id,
            request=DraftSendRequest(
                expected_version=1,
                target_connector="gmail",
                target_metadata={},
            ),
        )

    def event_types(self) -> list[str]:
        return [_type_of(e) for e in self.store.events_by_run.get(_RUN, [])]

    def approvals(self) -> list[object]:
        return list(self.store.approval_requests.values())


def _type_of(event: object) -> str:
    value = getattr(getattr(event, "event_type", None), "value", None)
    return value if isinstance(value, str) else str(getattr(event, "event_type", ""))


class TestFlagOffV1Path:
    async def test_flag_off_creates_v1_approval_and_no_v2_event(
        self, monkeypatch
    ) -> None:
        monkeypatch.delenv("SURFACES_V2", raising=False)
        h = Harness(wire_stager=True)  # stager wired, but flag off ⇒ v1 path
        await h.seed_draft()
        result = await h.send()

        assert result.stage_id is None
        assert result.approval_id is not None
        types = h.event_types()
        assert "approval_requested" in types
        assert "write.staged" not in types
        assert "revision.added" not in types
        assert "write.applied" not in types
        approvals = h.approvals()
        assert len(approvals) == 1
        # Draft never sent — nothing executed.
        latest = await h.drafts.latest(org_id=_ORG, draft_id=h.draft_id)
        assert latest.status is DraftStatus.SEND_PENDING_APPROVAL

    async def test_flag_on_but_stager_unwired_stays_v1(self, monkeypatch) -> None:
        monkeypatch.setenv("SURFACES_V2", "true")
        h = Harness(wire_stager=False)  # no stager ⇒ v1 regardless of the flag
        await h.seed_draft()
        result = await h.send()

        assert result.stage_id is None
        assert result.approval_id is not None
        assert "approval_requested" in h.event_types()
        assert "write.staged" not in h.event_types()


class TestFlagOnV2Path:
    async def test_flag_on_stages_write_and_returns_stage_id(self, monkeypatch) -> None:
        monkeypatch.setenv("SURFACES_V2", "true")
        h = Harness(wire_stager=True)
        await h.seed_draft()
        result = await h.send()

        assert result.stage_id is not None
        assert result.approval_id is None
        types = h.event_types()
        assert "write.staged" in types
        assert "revision.added" in types
        # No v1 approval row, no APPROVAL_REQUESTED event.
        assert "approval_requested" not in types
        assert h.approvals() == []
        # Nothing executed: no write.applied, draft still pending.
        assert "write.applied" not in types
        latest = await h.drafts.latest(org_id=_ORG, draft_id=h.draft_id)
        assert latest.status is DraftStatus.SEND_PENDING_APPROVAL
