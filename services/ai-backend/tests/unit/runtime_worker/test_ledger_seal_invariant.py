"""The causal-prefix seal, guarded at the topology that broke it.

The defect these tests exist for: ``publish_artifact`` committed its outbox rows
mid-run, but the rows only became ledger events when a worker next called
``claim_next``. Under ``RUNTIME_START_IN_PROCESS_WORKER=true`` — the desktop
topology — the single worker cannot claim while it is executing, so
``artifact.created`` and ``artifact.presentation_decided`` landed *after*
``run_completed``. The SSE stream had already closed on the terminal event, so
no live client received them, and the Studio canvas correctly reported "no
artifact was created" about an artifact that demonstrably existed.

A unit test with a fake queue would have passed throughout. These assert the
invariant itself — the terminal event is the last event of the causal prefix —
and the two mechanisms that now keep it true: pre-seal draining and fail-closed
enforcement at the append funnel.
"""

from __future__ import annotations

import pytest

from agent_runtime.api.artifact_ledger_publisher import ArtifactOutboxProjectionDrain
from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ledger_seal import (
    LedgerAmendment,
    LedgerAmendmentReason,
    LedgerSealViolation,
)
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.api.run_termination import (
    RunTerminationCoordinator,
    TerminationReason,
)
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    CreateConversationRequest,
    CreateRunRequest,
    RunRecord,
    RuntimeApiEventType,
)
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.loop import RuntimeWorker

from tests.unit.runtime_worker.test_fake_model_run_stream import FakeModelRunMixin


@pytest.fixture(autouse=True)
def _fake_model(monkeypatch) -> None:
    """Run creation consults the real credential gate; the fake model opens it."""

    monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")


class SealedRunMixin:
    """Seed real runs and drive them to a sealed state."""

    TERMINAL_EVENT_TYPES = frozenset(
        {"run_completed", "run_failed", "run_cancelled", "run_rejected"}
    )
    ARTIFACT_PAYLOAD = {
        "v": 1,
        "artifact_id": "art_1",
        "kind": "dataset",
        "revision": 1,
        "content_ref": "artifact://art_1/revisions/1",
        "content_digest": "0" * 64,
        "author": "model",
    }

    @staticmethod
    def _seed_settings() -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_FAKE_MODEL": "1",
            }
        )

    @classmethod
    async def _seed_run(cls, store: InMemoryRuntimeApiStore) -> RunRecord:
        """Create a conversation + run through the real coordinators."""

        settings = cls._seed_settings()
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=producer,
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )
        conversation = await ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=run_coordinator
        ).create_conversation(
            CreateConversationRequest(
                org_id="org_123", user_id="user_123", assistant_id="assistant_123"
            )
        )
        created = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id="org_123",
                user_id="user_123",
                user_input="Publish something.",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        run = await store.get_run(org_id="org_123", run_id=created.run_id)
        assert run is not None
        return run

    @classmethod
    async def _sealed_run(
        cls, store: InMemoryRuntimeApiStore
    ) -> tuple[RuntimeEventProducer, RunRecord]:
        """Return a producer whose run has already had its prefix sealed."""

        producer = RuntimeEventProducer(persistence=store, event_store=store)
        run = await cls._seed_run(store)
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.RUN_COMPLETED,
            payload={"status": "run_completed"},
        )
        return producer, run

    @staticmethod
    def _event_names(store: InMemoryRuntimeApiStore, run_id: str) -> list[str]:
        return [event.event_type for event in store.events_by_run[run_id]]


class PendingArtifactOutboxMixin:
    """A canonical outbox holding a row the inline publish never delivered."""

    class _PendingArtifactCommand:
        """The shape ``pending_artifact_events`` yields, narrowed to what drains."""

        def __init__(self, *, run: RunRecord, payload: dict) -> None:
            self.run_id = run.run_id
            self.org_id = run.org_id
            self.event_id = "artevt_" + "a" * 40
            self.event_type = RuntimeApiEventType.ARTIFACT_CREATED
            self.created_at = run.created_at
            self.payload = payload

    class _PendingOutbox:
        def __init__(self, *, commands: tuple) -> None:
            self._commands = commands

        async def pending_artifact_events(self):
            return self._commands

    @classmethod
    def _pending_outbox(cls, *, run: RunRecord, payload: dict):
        return cls._PendingOutbox(
            commands=(cls._PendingArtifactCommand(run=run, payload=payload),)
        )


class TestSealHoldsOnARealRun(FakeModelRunMixin, SealedRunMixin):
    """The invariant, asserted against the real worker/graph/streamer."""

    async def test_terminal_event_is_the_last_event_of_the_prefix(self) -> None:
        store = InMemoryRuntimeApiStore()
        settings = self._settings()
        run_id = await self._enqueue_run(store, settings)

        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            mcp_discovery_cache=(
                DefaultRuntimeDependenciesFactory.build_default_discovery_cache()
            ),
        )
        await worker.run_until_idle()

        names = self._event_names(store, run_id)
        positions = [
            index
            for index, name in enumerate(names)
            if name in self.TERMINAL_EVENT_TYPES
        ]
        assert positions, names
        # The whole point: nothing follows the seal. Before the fix this held
        # for every producer except the artifact outbox, whose events appended
        # at N+1 and N+2 where no live client could reach them.
        assert positions[-1] == len(names) - 1, (
            f"events appended after the seal: {names[positions[-1] + 1 :]}"
        )


class TestEnforcementIsFailClosed(SealedRunMixin):
    """A causal append to a sealed run raises instead of going unseen."""

    async def test_causal_append_after_the_seal_raises(self) -> None:
        store = InMemoryRuntimeApiStore()
        producer, run = await self._sealed_run(store)

        with pytest.raises(LedgerSealViolation) as caught:
            await producer.append_api_event(
                run=run,
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType.ARTIFACT_CREATED,
                payload=dict(self.ARTIFACT_PAYLOAD),
            )
        assert caught.value.sealed_by == "run_completed"
        assert caught.value.safe_message == (
            "This run's ledger is sealed and cannot accept further causal events."
        )

    async def test_declared_amendment_after_the_seal_is_allowed_and_labelled(
        self,
    ) -> None:
        store = InMemoryRuntimeApiStore()
        producer, run = await self._sealed_run(store)

        envelope = await producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.ARTIFACT_CREATED,
            payload=dict(self.ARTIFACT_PAYLOAD),
            amendment=LedgerAmendment(
                reason=LedgerAmendmentReason.LATE_CAUSAL_RECOVERY
            ),
        )
        # Legible in replay and audit export: a reader can tell this fact came
        # after the seal without reconstructing append timing.
        assert envelope.metadata["ledger_amendment_reason"] == "late_causal_recovery"

    async def test_an_unsealed_run_accepts_causal_appends(self) -> None:
        store = InMemoryRuntimeApiStore()
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        run = await self._seed_run(store)

        envelope = await producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.FINAL_RESPONSE,
            payload={"message": "hi"},
        )
        assert envelope.sequence_no >= 1


class TestOutboxDrainsBeforeTheSeal(SealedRunMixin, PendingArtifactOutboxMixin):
    """The direct regression: the artifact reaches the prefix, not the tail."""

    async def test_pending_artifact_rows_are_flushed_before_the_terminal_event(
        self,
    ) -> None:
        store = InMemoryRuntimeApiStore()
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        run = await self._seed_run(store)

        coordinator = RunTerminationCoordinator(event_producer=producer)
        coordinator.register_projection_drain(
            ArtifactOutboxProjectionDrain(
                canonical_outbox=self._pending_outbox(
                    run=run, payload=dict(self.ARTIFACT_PAYLOAD)
                ),
                event_producer=producer,
            )
        )
        await coordinator.terminate(
            run=run,
            terminal_status=AgentRunStatus.COMPLETED,
            reason=TerminationReason.NORMAL_COMPLETION,
        )

        names = self._event_names(store, run.run_id)
        assert "artifact.created" in names, names
        assert names.index("artifact.created") < names.index("run_completed"), names
