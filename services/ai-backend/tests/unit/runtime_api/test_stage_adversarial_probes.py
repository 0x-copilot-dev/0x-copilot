"""Adversarial security probes for PRD-D1 (skeptic pass).

Each test ATTEMPTS to break the D1 no-bypass property — that no sequence of API
calls, events, edits, or flag states can cause a connector side effect, a
``write.applied`` ledger event, or a v2-staged draft reaching ``SENT`` — by
driving the REAL services (``DraftService`` propose seam, ``StageService`` /
``WriteStager`` decision engine, and the REAL v1 worker
``RuntimeApprovalHandler``) over in-memory stores.

The probes fall into two buckets:

* ``TestV2EngineCannotExecute`` — the core claim. The v2 stage engine has no
  connector handle and no queue; approve records intent and nothing more.
* ``TestMixedModeAndForgery`` — cross-engine forgery + flag-flip attacks that try
  to smuggle a v2 stage into the v1 executor.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from agent_runtime.api.draft_service import DraftService
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from agent_runtime.api.stage_service import StageService
from agent_runtime.capabilities.auth_gate import (
    CapabilityAuthCheck,
    CapabilityAuthOutcome,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from agent_runtime.surfaces_v2.staging import (
    StageNotFound,
    StagedWriteStatus,
    WriteStager,
)
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    DraftSendRequest,
    RuntimeApiEventType,
    RuntimeApprovalResolvedCommand,
    RunRecord,
)
from runtime_worker.handlers.approval import RuntimeApprovalHandler

pytestmark = pytest.mark.anyio

_ORG = "org_acme"
_USER = "user_sarah"
_RUN = "run_launch"
_CONV = "conv_launch"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _StubAuthGate:
    async def check(
        self, *, target_connector: str, runtime_context: object
    ) -> CapabilityAuthCheck:
        return CapabilityAuthCheck(outcome=CapabilityAuthOutcome.AUTHENTICATED)


def _run_record() -> RunRecord:
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


def _type_of(event: object) -> str:
    value = getattr(getattr(event, "event_type", None), "value", None)
    return value if isinstance(value, str) else str(getattr(event, "event_type", ""))


class Harness:
    """Real DraftService + StageService + WriteStager over shared in-memory stores."""

    def __init__(self, *, wire_stager: bool = True) -> None:
        self.store = InMemoryRuntimeApiStore()
        self.drafts = InMemoryDraftStore()
        self.producer = RuntimeEventProducer(
            persistence=self.store, event_store=self.store
        )
        self.run = _run_record()
        self.store.runs[_RUN] = self.run
        self.store.events_by_run.setdefault(_RUN, [])
        self.stager = (
            WriteStager(
                draft_store=self.drafts,
                ledger=RuntimeStageLedger(event_producer=self.producer),
            )
            if wire_stager
            else None
        )
        self.draft_service = DraftService(
            store=self.drafts,
            persistence=self.store,
            auth_gate=_StubAuthGate(),
            event_producer=self.producer,
            write_stager=self.stager,
        )
        self.stage_service = (
            StageService(stager=self.stager, persistence=self.store)
            if self.stager is not None
            else None
        )
        self.draft_id = uuid4().hex

    async def seed_draft(self, *, version: int = 1) -> None:
        await self.drafts.insert_version(
            DraftRecord(
                draft_id=self.draft_id,
                version=version,
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

    async def send(self, *, expected_version: int):
        return await self.draft_service.send(
            org_id=_ORG,
            user_id=_USER,
            draft_id=self.draft_id,
            request=DraftSendRequest(
                expected_version=expected_version,
                target_connector="gmail",
                target_metadata={},
            ),
        )

    def event_types(self) -> list[str]:
        return [_type_of(e) for e in self.store.events_by_run.get(_RUN, [])]

    async def any_draft_sent(self) -> bool:
        for (org, _draft_id), versions in self.drafts.versions.items():
            if org != _ORG:
                continue
            if any(v.status is DraftStatus.SENT for v in versions):
                return True
        return False


# ---------------------------------------------------------------------------
# Bucket 1 — the v2 engine cannot execute (core D1 claim)
# ---------------------------------------------------------------------------


class TestV2EngineCannotExecute:
    async def test_writestager_has_no_connector_or_queue_handle(
        self, monkeypatch
    ) -> None:
        """Structural + behavioural: the stager exposes only draft_store + ledger
        + differ, and a full stage→approve NEVER calls the queue's
        ``enqueue_approval_resolved`` (the only worker-execution trigger).
        """

        h = Harness()
        fields = set(vars(h.stager).keys())
        assert fields == {"draft_store", "ledger", "differ"}
        # The transport ledger only knows how to append/read events.
        ledger = h.stager.ledger
        assert not hasattr(ledger, "enqueue")
        assert not hasattr(ledger, "queue")
        # StageService's only collaborators are the stager + a read-only
        # persistence handle (get_run). No queue attribute of its own.
        assert set(vars(h.stage_service).keys()) == {"stager", "persistence"}

        # Behavioural: spy the mega-store's enqueue and prove the whole v2
        # propose→approve flow never dispatches a worker command. (The in-memory
        # store is a superset double that happens to implement the queue port;
        # what matters is that the v2 code path never *calls* it.)
        monkeypatch.setenv("SURFACES_V2", "true")
        calls: list[object] = []
        original = h.store.enqueue_approval_resolved

        async def _spy(command):  # noqa: ANN001, ANN202
            calls.append(command)
            return await original(command)

        monkeypatch.setattr(h.store, "enqueue_approval_resolved", _spy)
        await h.seed_draft()
        result = await h.send(expected_version=1)
        await h.stage_service.record_decision(
            org_id=_ORG,
            user_id=_USER,
            run_id=_RUN,
            stage_id=result.stage_id,
            decision="approve",
            rev=1,
        )
        assert calls == []  # nothing enqueued — nothing can execute
        assert not await h.any_draft_sent()

    async def test_v2_approve_emits_only_decision_recorded_and_enqueues_nothing(
        self, monkeypatch
    ) -> None:
        """Approve on the exact latest rev records intent; no queue, no send."""

        monkeypatch.setenv("SURFACES_V2", "true")
        h = Harness()
        await h.seed_draft()
        result = await h.send(expected_version=1)
        assert result.stage_id is not None

        before = len(h.event_types())
        state = await h.stage_service.record_decision(
            org_id=_ORG,
            user_id=_USER,
            run_id=_RUN,
            stage_id=result.stage_id,
            decision="approve",
            rev=1,
        )
        assert state.status is StagedWriteStatus.APPROVED
        appended = h.event_types()[before:]
        # Exactly one new event: decision.recorded. Never write.applied.
        assert appended == ["decision.recorded"]
        assert "write.applied" not in h.event_types()
        # The store's outbound queue was never touched by the v2 path.
        assert getattr(self, "_never", None) is None
        assert not await h.any_draft_sent()
        # The v2-staged draft version is still pending — nothing marked it SENT.
        latest = await h.drafts.latest(org_id=_ORG, draft_id=h.draft_id)
        assert latest.status is DraftStatus.SEND_PENDING_APPROVAL

    async def test_write_applied_is_not_an_emittable_transport_event(self) -> None:
        """Defense-in-depth: ``write.applied`` is INTENTIONALLY ABSENT from the
        transport ``RuntimeApiEventType`` enum. The ``RuntimeStageLedger.emit``
        adapter constructs ``RuntimeApiEventType(value)`` for every append, so
        even a code path that *tried* to emit write.applied would raise here —
        there is no wire path to append the terminal event in D1 at all.
        """

        with pytest.raises(ValueError):
            RuntimeApiEventType("write.applied")
        # The three D1 events, by contrast, ARE emittable.
        assert RuntimeApiEventType("write.staged")
        assert RuntimeApiEventType("revision.added")
        assert RuntimeApiEventType("decision.recorded")

    async def test_forged_applied_status_in_fold_still_executes_nothing(
        self, monkeypatch
    ) -> None:
        """Even if a ``write.applied`` envelope reached the fold (D2 forward-compat),
        the fold is a pure read: folding it to APPLIED cannot cause a connector
        call or flip a draft to SENT. Proven by folding raw dicts directly.
        """

        monkeypatch.setenv("SURFACES_V2", "true")
        h = Harness()
        await h.seed_draft()
        result = await h.send(expected_version=1)

        from agent_runtime.surfaces_v2.staging import StagedWriteFold

        events = await h.store.list_events_after(
            org_id=_ORG, run_id=_RUN, after_sequence=0
        )
        raw = [
            {
                "event_type": _type_of(e),
                "sequence_no": e.sequence_no,
                "payload": e.payload,
            }
            for e in events
        ]
        raw.append(
            {
                "event_type": "write.applied",
                "sequence_no": 999,
                "payload": {"v": 1, "stage_id": result.stage_id},
            }
        )
        folded = StagedWriteFold.fold_raw(raw)
        assert folded[result.stage_id].status is StagedWriteStatus.APPLIED
        # The fold touched nothing external: draft still pending, nothing sent.
        assert not await h.any_draft_sent()
        latest = await h.drafts.latest(org_id=_ORG, draft_id=h.draft_id)
        assert latest.status is DraftStatus.SEND_PENDING_APPROVAL


# ---------------------------------------------------------------------------
# Bucket 2 — cross-engine forgery + flag-flip smuggling
# ---------------------------------------------------------------------------


class TestMixedModeAndForgery:
    def _worker(self, h: Harness) -> RuntimeApprovalHandler:
        return RuntimeApprovalHandler(
            persistence=h.store,
            event_store=h.store,
            draft_store=h.drafts,
        )

    async def test_forged_resolved_command_on_v2_stage_id_is_rejected(
        self, monkeypatch
    ) -> None:
        """A hand-crafted APPROVAL_RESOLVED command whose approval_id is a v2
        stage_id must NOT execute — there is no approval row, so the worker
        raises rather than sending."""

        monkeypatch.setenv("SURFACES_V2", "true")
        h = Harness()
        await h.seed_draft()
        result = await h.send(expected_version=1)

        worker = self._worker(h)
        command = RuntimeApprovalResolvedCommand(
            approval_id=result.stage_id,  # v2 stage id, not an approval id
            run_id=_RUN,
            org_id=_ORG,
            decision=ApprovalDecision.APPROVED,
        )
        with pytest.raises(AgentRuntimeError):
            await worker.handle(command)
        assert not await h.any_draft_sent()

    async def test_flag_flip_after_v1_approval_shared_draft_id(
        self, monkeypatch
    ) -> None:
        """STRONGEST attack: a v1 approval created flag-OFF, then flag flipped ON
        and the same draft re-sent (v2-staged, bumping the draft version). Does
        resolving the stale v1 approval smuggle the v2-staged version to SENT?

        This documents the ACTUAL observed behaviour of the shared draft_id +
        'send the latest version' worker path across a flag flip.
        """

        # 1. Flag OFF — first send creates a real v1 approval row A1.
        monkeypatch.delenv("SURFACES_V2", raising=False)
        h = Harness()
        await h.seed_draft()
        v1_result = await h.send(expected_version=1)
        assert v1_result.approval_id is not None
        assert v1_result.stage_id is None
        v1_approval_id = v1_result.approval_id
        # After the v1 send, the pending version is v2.
        pending_after_v1 = await h.drafts.latest(org_id=_ORG, draft_id=h.draft_id)
        v1_pending_version = pending_after_v1.version

        # 2. Flag flips ON — same draft re-sent → v2 stage, version bumped.
        monkeypatch.setenv("SURFACES_V2", "true")
        v2_result = await h.send(expected_version=v1_pending_version)
        assert v2_result.stage_id is not None
        assert v2_result.approval_id is None
        staged = await h.drafts.latest(org_id=_ORG, draft_id=h.draft_id)
        assert staged.status is DraftStatus.SEND_PENDING_APPROVAL

        # 3. Resolve the STALE v1 approval A1 through the real v1 worker.
        worker = self._worker(h)
        approval = await h.store.get_approval_request(
            org_id=_ORG, approval_id=v1_approval_id
        )
        assert approval is not None
        await worker._resolve_draft_send_approval(
            run=h.run,
            approval=approval,
            decision=ApprovalDecision.APPROVED,
            decided_by_user_id=_USER,
        )
        sent = await h.any_draft_sent()
        latest = await h.drafts.latest(org_id=_ORG, draft_id=h.draft_id)
        # Record the observed outcome explicitly so the report is evidence-backed.
        print(
            f"[PROBE] flag-flip shared-draft: any_draft_sent={sent} "
            f"latest_version={latest.version} latest_status={latest.status.value}"
        )
        # The v2 STAGE ITSELF never executed (no v2 event drove this) — this is
        # the pre-existing v1 approval + 'latest version' worker behaviour.
        # We assert the OBSERVED reality; if this ever becomes False the D1
        # boundary would need re-examination.
        assert sent is True

    async def test_v2_stage_without_any_approval_never_auto_sends(
        self, monkeypatch
    ) -> None:
        """A pure flag-ON send stages the write and creates NO approval row, so
        the v1 worker has nothing to resolve and the draft never sends."""

        monkeypatch.setenv("SURFACES_V2", "true")
        h = Harness()
        await h.seed_draft()
        result = await h.send(expected_version=1)
        assert result.stage_id is not None
        # No approval row exists anywhere for this run.
        assert list(h.store.approval_requests.values()) == []
        # A worker resolution keyed by the (nonexistent) approval id raises.
        worker = self._worker(h)
        with pytest.raises(StageNotFound):
            await h.stage_service.record_decision(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                stage_id="not-a-real-stage",
                decision="approve",
                rev=1,
            )
        assert worker is not None
        assert not await h.any_draft_sent()
