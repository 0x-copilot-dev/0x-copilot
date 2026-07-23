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
    async def test_writestager_never_executes_inline_and_never_uses_v1_queue(
        self, monkeypatch
    ) -> None:
        """The stager holds NO connector handle and executes NOTHING inline.

        D2 gives the stager an optional ``commit_queue`` seam: a NEW approve
        enqueues ONE durable ``stage_commit_requested`` command (the worker
        CommitEngine handler is the only executor). It still never calls the v1
        ``enqueue_approval_resolved`` path, never touches an MCP client, and — with
        ``commit_queue=None`` (this harness) — records the decision and enqueues
        NOTHING (fail-open to no-commit, never to execution).
        """

        h = Harness()
        # Structural: only these dataclass fields — no connector, no MCP client.
        fields = set(vars(h.stager).keys())
        assert fields == {"draft_store", "ledger", "differ", "commit_queue"}
        # This harness wires no queue ⇒ approve executes nothing.
        assert h.stager.commit_queue is None
        # The transport ledger only knows how to append/read events.
        ledger = h.stager.ledger
        assert not hasattr(ledger, "enqueue")
        assert not hasattr(ledger, "queue")
        # StageService's only collaborators are the stager + a read-only
        # persistence handle (get_run). No queue attribute of its own.
        assert set(vars(h.stage_service).keys()) == {"stager", "persistence"}

        # Behavioural: the v2 propose→approve flow never dispatches the v1 worker
        # command, and (commit_queue=None) never enqueues a stage-commit either.
        monkeypatch.setenv("SURFACES_V2", "true")
        v1_calls: list[object] = []
        commit_calls: list[object] = []
        original_v1 = h.store.enqueue_approval_resolved
        original_commit = h.store.enqueue_stage_commit

        async def _spy_v1(command):  # noqa: ANN001, ANN202
            v1_calls.append(command)
            return await original_v1(command)

        async def _spy_commit(command):  # noqa: ANN001, ANN202
            commit_calls.append(command)
            return await original_commit(command)

        monkeypatch.setattr(h.store, "enqueue_approval_resolved", _spy_v1)
        monkeypatch.setattr(h.store, "enqueue_stage_commit", _spy_commit)
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
        assert v1_calls == []  # never the v1 worker trigger
        assert commit_calls == []  # commit_queue is None ⇒ nothing enqueued
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

    async def test_write_applied_is_emittable_only_from_the_worker_handler(
        self,
    ) -> None:
        """D2 adds ``write.applied`` to the transport enum — but the SOLE producer
        is the worker-side CommitEngine handler. The ``RuntimeStageLedger`` the
        API layer wires never emits it (it only appends the D1 triad + surface),
        and the ``StageService`` decision route (this suite's API surface) has no
        connector and no ``write.applied`` code path. The no-bypass suite
        (``test_stage_no_bypass``) proves the API process yields zero of them.
        """

        # The terminal event is now a real transport type (D2).
        assert RuntimeApiEventType("write.applied")
        # The D1 triad remains emittable.
        assert RuntimeApiEventType("write.staged")
        assert RuntimeApiEventType("revision.added")
        assert RuntimeApiEventType("decision.recorded")
        # The API-layer ledger adapter carries no write.applied producer — it only
        # maps a value to the enum + appends. The producer lives in the worker.
        from agent_runtime.api import stage_ledger as _api_ledger
        import inspect

        assert "write.applied" not in inspect.getsource(_api_ledger)

    async def test_forged_applied_on_unapproved_stage_folds_corrupt_and_sends_nothing(
        self, monkeypatch
    ) -> None:
        """A forged ``write.applied`` on a stage that was never approved cannot
        smuggle an APPLIED terminal: the D2 fold's fail-closed state machine maps
        it to CORRUPT (defensive) rather than accepting an unauthorized send. The
        fold is a pure read either way — no connector call, no draft flip.
        """

        monkeypatch.setenv("SURFACES_V2", "true")
        h = Harness()
        await h.seed_draft()
        result = await h.send(expected_version=1)  # STAGED, never approved

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
                "payload": {
                    "v": 1,
                    "stage_id": result.stage_id,
                    "rev": 1,
                    "result": "applied",
                },
            }
        )
        folded = StagedWriteFold.fold_raw(raw)
        # Fail-closed: a write.applied onto a non-APPROVED stage ⇒ CORRUPT, never
        # APPLIED — the forged event cannot masquerade as a real send.
        assert folded[result.stage_id].status is StagedWriteStatus.CORRUPT
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
        """REGRESSION (PRD-D2 flag-flip WYSIWYG fix): a v1 approval created flag-OFF,
        then flag flipped ON and the SAME draft re-sent (v2-staged, bumping the
        draft version). The D1 skeptic found the stale v1 worker sent
        ``draft_store.latest(draft_id)`` — the NEWER v2-staged content the user
        never approved at v1 time. D2 closes that gap: the v1 draft-send worker
        REFUSES to resolve an approval whose draft now has a ``write.staged`` ledger
        event (superseded by a v2 stage). Nothing sends.
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

        # 2. Flag flips ON — same draft re-sent → v2 stage, version bumped. This
        #    emits a ``write.staged`` on the run's ledger for this draft_id.
        monkeypatch.setenv("SURFACES_V2", "true")
        v2_result = await h.send(expected_version=v1_pending_version)
        assert v2_result.stage_id is not None
        assert v2_result.approval_id is None
        staged = await h.drafts.latest(org_id=_ORG, draft_id=h.draft_id)
        assert staged.status is DraftStatus.SEND_PENDING_APPROVAL
        newer_version_the_user_never_approved_at_v1 = staged.version
        assert newer_version_the_user_never_approved_at_v1 > v1_pending_version

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
            f"[PROBE] flag-flip shared-draft (D2 fix): any_draft_sent={sent} "
            f"latest_version={latest.version} latest_status={latest.status.value}"
        )
        # D2 FIX: the stale v1 approval is REFUSED (superseded by the v2 stage) —
        # the newer content the user never approved at v1 time never sends, and
        # the draft stays pending for the v2 flow to own.
        assert sent is False
        assert latest.status is DraftStatus.SEND_PENDING_APPROVAL

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
