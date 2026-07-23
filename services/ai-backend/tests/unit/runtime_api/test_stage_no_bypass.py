"""Adversarial no-bypass suite for the PRD-D1 staged-write engine (DoD core).

The fail-closed guarantee this PR must prove: **nothing executes**. No sequence
of stage / revise / decide operations — including an approve on the exact latest
rev — produces a connector side effect or a ``write.applied`` ledger event. D2's
CommitEngine is the sole producer of ``write.applied``; in D1 no producer emits
it and no MCP client is reachable from the stager.

This ports + extends the PRD-09 v1 no-bypass discipline
(``test_approval_with_edits.py`` / ``test_draft_send_approve_with_edits.py`` stay
green untouched) to the v2 staging model. It drives the real
:class:`StageService` over the real transport ledger (``RuntimeStageLedger`` →
``RuntimeEventProducer`` → in-memory event store, so the projector allow-list
runs) plus an in-memory draft store, and it wires a **spying MCP client** onto
the harness to assert it is never touched (structurally it cannot be — the stager
has no connector handle at all).
"""

from __future__ import annotations

import random
from uuid import uuid4

import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from agent_runtime.api.stage_service import StageService
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from agent_runtime.surfaces_v2.staging import (
    StagedWriteError,
    StagedWriteStatus,
    WriteStager,
)
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord

pytestmark = pytest.mark.anyio

_ORG = "org_acme"
_USER = "user_sarah"
_RUN = "run_launch"
_CONV = "conv_launch"

# Event types D1 is allowed to emit. ``write.applied`` is deliberately absent.
_D1_EMITTABLE = {
    "surface.created",
    "write.staged",
    "revision.added",
    "decision.recorded",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _SpyMcpClient:
    """Records ANY invocation. The stager never receives this; if a single call
    is ever recorded the fail-closed property is broken."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):  # noqa: ANN001 - test spy
        async def _record(*args, **kwargs):  # noqa: ANN002, ANN003
            self.calls.append((name, args, kwargs))
            return None

        return _record


class NoBypassHarness:
    """Real StageService over an in-memory store + a spying (unwired) MCP client."""

    def __init__(self) -> None:
        self.store = InMemoryRuntimeApiStore()
        self.drafts = InMemoryDraftStore()
        self.mcp = _SpyMcpClient()  # deliberately never passed to the stager
        producer = RuntimeEventProducer(persistence=self.store, event_store=self.store)
        stager = WriteStager(
            draft_store=self.drafts,
            ledger=RuntimeStageLedger(event_producer=producer),
        )
        self.service = StageService(stager=stager, persistence=self.store)
        self.stager = stager
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

    async def seed_and_stage(self, content: str = "Dear team, launch Friday."):
        draft_id = uuid4().hex
        record = await self.drafts.insert_version(
            DraftRecord(
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
        )
        return await self.stager.stage(
            run=self.run,
            org_id=_ORG,
            run_id=_RUN,
            draft=record,
            target_connector="gmail",
            target_op="send",
        )

    def event_types(self) -> list[str]:
        return [_type_of(event) for event in self.store.events_by_run.get(_RUN, [])]

    async def any_draft_sent(self) -> bool:
        for (org, _draft_id), versions in self.drafts.versions.items():
            if org != _ORG:
                continue
            if any(v.status is DraftStatus.SENT for v in versions):
                return True
        return False


def _type_of(event: object) -> str:
    value = getattr(getattr(event, "event_type", None), "value", None)
    return value if isinstance(value, str) else str(getattr(event, "event_type", ""))


class TestNoBypass:
    async def test_no_event_type_write_applied_is_emittable_in_d1(self) -> None:
        # Exercise every legal transition (stage → edit → approve latest rev) and
        # assert the emitted set is exactly the D1-allowed types — never
        # write.applied — and no MCP call was recorded.
        h = NoBypassHarness()
        state = await h.seed_and_stage()
        state = await h.service.add_user_revision(
            org_id=_ORG,
            user_id=_USER,
            run_id=_RUN,
            stage_id=state.stage_id,
            base_rev=1,
            content_text="Dear team, launch Monday.",
            title=None,
        )
        state = await h.service.record_decision(
            org_id=_ORG,
            user_id=_USER,
            run_id=_RUN,
            stage_id=state.stage_id,
            decision="approve",
            rev=state.latest_rev,
        )
        assert state.status is StagedWriteStatus.APPROVED
        emitted = set(h.event_types())
        assert "write.applied" not in emitted
        assert emitted <= _D1_EMITTABLE
        assert h.mcp.calls == []  # nothing executed
        assert not await h.any_draft_sent()

    async def test_random_api_sequences_never_yield_write_applied(self) -> None:
        # Property-style: many random stage/revise/decide sequences. Zero
        # write.applied events, zero MCP calls, no draft ever sent — whatever the
        # order, whatever the (often failing) decisions.
        rng = random.Random(20260723)
        for _ in range(60):
            h = NoBypassHarness()
            state = await h.seed_and_stage()
            stage_id = state.stage_id
            for _step in range(rng.randint(1, 8)):
                op = rng.choice(
                    ["revise", "approve", "reject", "restore", "hold", "get"]
                )
                try:
                    if op == "revise":
                        await h.service.add_user_revision(
                            org_id=_ORG,
                            user_id=_USER,
                            run_id=_RUN,
                            stage_id=stage_id,
                            base_rev=rng.randint(1, 4),
                            content_text=f"edit {_step} {rng.random()}",
                            title=None,
                        )
                    elif op == "get":
                        await h.service.get_state(
                            org_id=_ORG,
                            user_id=_USER,
                            run_id=_RUN,
                            stage_id=stage_id,
                        )
                    else:
                        await h.service.record_decision(
                            org_id=_ORG,
                            user_id=_USER,
                            run_id=_RUN,
                            stage_id=stage_id,
                            decision=op,
                            rev=rng.choice([None, 1, 2, 3]),
                        )
                except StagedWriteError:
                    pass  # typed domain rejection — expected on many sequences
            assert "write.applied" not in h.event_types()
            assert set(h.event_types()) <= _D1_EMITTABLE
            assert h.mcp.calls == []
            assert not await h.any_draft_sent()

    async def test_approve_stale_rev_rejected_409_no_event(self) -> None:
        h = NoBypassHarness()
        state = await h.seed_and_stage()
        await h.service.add_user_revision(
            org_id=_ORG,
            user_id=_USER,
            run_id=_RUN,
            stage_id=state.stage_id,
            base_rev=1,
            content_text="new body",
            title=None,
        )
        before = len(h.event_types())
        from agent_runtime.surfaces_v2.staging import StaleRevision

        with pytest.raises(StaleRevision):
            await h.service.record_decision(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                stage_id=state.stage_id,
                decision="approve",
                rev=1,  # latest is 2 → stale, no pin
            )
        assert len(h.event_types()) == before  # nothing appended on the 409
        assert h.mcp.calls == []

    async def test_decision_on_v1_approval_id_does_not_touch_v2_stage(self) -> None:
        # A v1 approval id smuggled in as a stage_id resolves to no v2 stage → 404,
        # zero events appended, nothing executed.
        h = NoBypassHarness()
        await h.seed_and_stage()
        before = len(h.event_types())
        from agent_runtime.surfaces_v2.staging import StageNotFound

        with pytest.raises(StageNotFound):
            await h.service.record_decision(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                stage_id="mcp_auth:run_launch:seed:linear",  # v1-shaped id
                decision="approve",
                rev=1,
            )
        assert len(h.event_types()) == before
        assert h.mcp.calls == []

    async def test_edits_after_approve_rejected(self) -> None:
        h = NoBypassHarness()
        state = await h.seed_and_stage()
        await h.service.record_decision(
            org_id=_ORG,
            user_id=_USER,
            run_id=_RUN,
            stage_id=state.stage_id,
            decision="approve",
            rev=1,
        )
        before = len(h.event_types())
        from agent_runtime.surfaces_v2.staging import StageFrozen

        with pytest.raises(StageFrozen):
            await h.service.add_user_revision(
                org_id=_ORG,
                user_id=_USER,
                run_id=_RUN,
                stage_id=state.stage_id,
                base_rev=1,
                content_text="sneaky post-approve edit",
                title=None,
            )
        assert len(h.event_types()) == before
        assert "write.applied" not in h.event_types()
        assert h.mcp.calls == []

    async def test_foreign_user_cannot_decide_403_no_event(self) -> None:
        from agent_runtime.surfaces_v2.staging import StageForbidden

        h = NoBypassHarness()
        state = await h.seed_and_stage()
        before = len(h.event_types())
        with pytest.raises(StageForbidden):
            await h.service.record_decision(
                org_id=_ORG,
                user_id="intruder",
                run_id=_RUN,
                stage_id=state.stage_id,
                decision="approve",
                rev=1,
            )
        assert len(h.event_types()) == before
        assert h.mcp.calls == []


class TestSingleProducer:
    """DoD (PRD-D2): ``write.applied`` has EXACTLY ONE producer — the worker-side
    CommitEngine handler. Definitions (the transport enum / projector allow-list /
    ledger contract) and the consumer (the fold) reference the type but never
    EMIT it. An emitter is a module that appends the event to a run's stream."""

    _HANDLER = "runtime_worker/handlers/stage_commit.py"

    def test_only_the_handler_emits_write_applied(self) -> None:
        # Precise: the only module that both appends events (``append_api_event``)
        # AND references the terminal type is the handler. A second producer would
        # necessarily do both and be caught here.
        import pathlib

        src = pathlib.Path(__file__).resolve().parents[3] / "src"
        assert src.is_dir(), src
        emitters: set[str] = set()
        for path in src.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "append_api_event" in text and (
                "WRITE_APPLIED" in text or "write.applied" in text
            ):
                emitters.add(str(path.relative_to(src)))
        assert emitters == {self._HANDLER}, sorted(emitters)

    def test_the_transport_enum_emission_construct_is_unique(self) -> None:
        # The exact construction the handler uses to emit the terminal event.
        import pathlib

        src = pathlib.Path(__file__).resolve().parents[3] / "src"
        marker = "RuntimeApiEventType(LedgerEventType.WRITE_APPLIED.value)"
        users = {
            str(p.relative_to(src))
            for p in src.rglob("*.py")
            if marker in p.read_text(encoding="utf-8")
        }
        assert users == {self._HANDLER}, sorted(users)

    def test_write_applied_not_emitted_by_the_api_layer_ledger(self) -> None:
        # The API-side ledger adapter (what the StageService wires) never names the
        # terminal event — it only appends the D1 triad + surface. The producer is
        # exclusively the worker handler.
        import inspect

        from agent_runtime.api import stage_ledger

        assert "write.applied" not in inspect.getsource(stage_ledger)
