"""Exactly-once publication tests for PRD-A2 artifact ledger commands."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_runtime.api.artifact_ledger_publisher import (
    ArtifactOutboxProjectionDrain,
    RuntimeArtifactLedgerPublisher,
)
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ledger_seal import LedgerAmendment
from agent_runtime.artifacts.contracts import ArtifactLedgerEvent, ArtifactScope
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.persistence.constants import Values as PersistenceValues
from agent_runtime.persistence.records import RuntimeWorkerClaim
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    RunRecord,
    RuntimeApiEventType,
    RuntimeArtifactEventCommand,
    RuntimeEventPresentationProjector,
)
from runtime_worker.handlers.artifact_event import RuntimeArtifactEventHandler
from runtime_worker.loop import RuntimeWorker

pytestmark = pytest.mark.anyio

ORG = "org_artifacts"
USER = "user_artifacts"
RUN = "run_artifacts"
CONVERSATION = "conv_artifacts"
TRACE = "trace_artifacts"
ARTIFACT_ID = "art_00000000-0000-4000-8000-000000000001"
EVENT_ID = f"artevt_{'d' * 64}"
CREATED_AT = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _run() -> RunRecord:
    return RunRecord(
        run_id=RUN,
        conversation_id=CONVERSATION,
        org_id=ORG,
        user_id=USER,
        user_message_id="msg_artifacts",
        trace_id=TRACE,
        status=AgentRunStatus.RUNNING,
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=AgentRuntimeContext(
            user_id=USER,
            org_id=ORG,
            roles=["employee"],
            run_id=RUN,
            trace_id=TRACE,
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


def _payload() -> dict[str, object]:
    return {
        "v": 1,
        "artifact_id": ARTIFACT_ID,
        "kind": "document",
        "revision": 1,
        "content_ref": f"artifact://{ARTIFACT_ID}/revisions/1",
        "content_digest": "e" * 64,
        "author": "model",
    }


def _command(**changes: object) -> RuntimeArtifactEventCommand:
    values: dict[str, object] = {
        "command_id": EVENT_ID,
        "event_id": EVENT_ID,
        "org_id": ORG,
        "user_id": USER,
        "run_id": RUN,
        "conversation_id": CONVERSATION,
        "trace_id": TRACE,
        "event_type": LedgerEventType.ARTIFACT_CREATED,
        "payload": _payload(),
        "created_at": CREATED_AT,
    }
    values.update(changes)
    return RuntimeArtifactEventCommand.model_validate(values)


async def test_retry_after_append_publishes_exactly_one_event() -> None:
    store = InMemoryRuntimeApiStore()
    store.runs[RUN] = _run()
    handler = RuntimeArtifactEventHandler(persistence=store, event_store=store)
    command = _command()

    await handler.handle(command)
    # Models a worker crash after append but before mark_complete: the outbox
    # lease expires and the exact command is delivered again.
    await handler.handle(command)

    events = await store.list_events_after(
        org_id=ORG,
        run_id=RUN,
        after_sequence=0,
    )
    assert len(events) == 1
    assert events[0].event_id == EVENT_ID
    assert events[0].event_type is RuntimeApiEventType.ARTIFACT_CREATED
    assert events[0].created_at == CREATED_AT
    assert events[0].payload == _payload()


async def test_scope_mismatch_fails_closed_without_append() -> None:
    store = InMemoryRuntimeApiStore()
    store.runs[RUN] = _run()
    handler = RuntimeArtifactEventHandler(persistence=store, event_store=store)

    with pytest.raises(AgentRuntimeError):
        await handler.handle(_command(user_id="user_foreign"))

    assert store.events_by_run.get(RUN, []) == []


def test_command_rejects_non_artifact_event_and_different_command_id() -> None:
    with pytest.raises(ValidationError):
        _command(event_type=LedgerEventType.OPERATION_COMPLETED)
    with pytest.raises(ValidationError):
        _command(command_id=f"artevt_{'f' * 64}")


def test_projector_rejects_uncontracted_payload_fields() -> None:
    projected = RuntimeEventPresentationProjector.payload_for_event(
        event_type=RuntimeApiEventType.ARTIFACT_CREATED,
        payload={**_payload(), "bytes": "must-not-ride-the-ledger"},
    )

    assert projected == {}


class CrossLanePublishMixin:
    """Build the three lanes that publish one artifact fact, sharing an event id.

    Inline publication (causal, mid-run), the pre-seal outbox drain (also
    causal) and the worker recovery lane (a ``late_causal_recovery`` amendment)
    all append the same ``artevt_`` event, so any of them can be the redelivery.
    """

    @staticmethod
    def _seeded_store() -> tuple[InMemoryRuntimeApiStore, RuntimeEventProducer]:
        store = InMemoryRuntimeApiStore()
        store.runs[RUN] = _run()
        return store, RuntimeEventProducer(persistence=store, event_store=store)

    @staticmethod
    def _ledger_event() -> ArtifactLedgerEvent:
        return ArtifactLedgerEvent(
            event_id=EVENT_ID,
            scope=ArtifactScope(
                org_id=ORG,
                user_id=USER,
                run_id=RUN,
                conversation_id=CONVERSATION,
                trace_id=TRACE,
            ),
            event_type=LedgerEventType.ARTIFACT_CREATED,
            payload=_payload(),
            created_at=CREATED_AT,
        )

    @staticmethod
    async def _publish_inline(
        store: InMemoryRuntimeApiStore,
        producer: RuntimeEventProducer,
        event: ArtifactLedgerEvent,
    ) -> None:
        await RuntimeArtifactLedgerPublisher(
            persistence=store, event_producer=producer
        ).publish(event)

    @staticmethod
    async def _publish_via_recovery_lane(
        store: InMemoryRuntimeApiStore,
        command: RuntimeArtifactEventCommand,
    ) -> None:
        await RuntimeArtifactEventHandler(persistence=store, event_store=store).handle(
            command
        )

    @staticmethod
    async def _drain_before_seal(
        store: InMemoryRuntimeApiStore,
        producer: RuntimeEventProducer,
        command: RuntimeArtifactEventCommand,
    ) -> None:
        class _PendingOutbox:
            async def pending_artifact_events(self):
                return (command,)

        run = await store.get_run(org_id=ORG, run_id=RUN)
        assert run is not None
        await ArtifactOutboxProjectionDrain(
            canonical_outbox=_PendingOutbox(), event_producer=producer
        ).drain_for_run(run=run)


class TestCrossLaneRedeliveryIsIdempotent(CrossLanePublishMixin):
    """The live crash: a redelivery of an already-stored artifact event.

    Observed on a real run as ``RuntimeEventIdempotencyConflict`` at
    ``append_api_event``, retried forever on the same two ``artevt_`` command
    ids. The event id is a digest of ``{run, artifact, revision, type,
    ordinal}`` and so is stable, but the recovery lane stamps
    ``ledger_amendment_reason`` into the body the inline lane had already
    stored, and the store compared metadata for equality.
    """

    async def test_recovery_command_after_inline_publish_is_a_replay(self) -> None:
        store, producer = self._seeded_store()
        await self._publish_inline(store, producer, self._ledger_event())

        await self._publish_via_recovery_lane(store, _command())

        events = await store.list_events_after(org_id=ORG, run_id=RUN, after_sequence=0)
        assert len(events) == 1
        assert events[0].event_id == EVENT_ID
        assert events[0].payload == _payload()
        # First lane to land the fact keeps authorship of the annotation: this
        # event *did* arrive inside the causal prefix, so claiming otherwise
        # would falsify the ledger.
        assert LedgerAmendment.Keys.REASON not in events[0].metadata

    async def test_recovery_command_after_pre_seal_drain_is_a_replay(self) -> None:
        store, producer = self._seeded_store()
        await self._drain_before_seal(store, producer, _command())

        await self._publish_via_recovery_lane(store, _command())

        assert len(store.events_by_run[RUN]) == 1

    async def test_inline_publish_after_recovery_lane_is_a_replay(self) -> None:
        store, producer = self._seeded_store()
        await self._publish_via_recovery_lane(store, _command())

        # The mirror direction, which the drain reaches on the pre-seal path:
        # an unannotated append meeting an annotated stored copy.
        await self._publish_inline(store, producer, self._ledger_event())
        await self._drain_before_seal(store, producer, _command())

        events = await store.list_events_after(org_id=ORG, run_id=RUN, after_sequence=0)
        assert len(events) == 1
        assert events[0].metadata[LedgerAmendment.Keys.REASON] == (
            "late_causal_recovery"
        )


async def test_worker_dispatches_existing_outbox_command_to_artifact_handler() -> None:
    class SpyHandler:
        def __init__(self) -> None:
            self.commands: list[RuntimeArtifactEventCommand] = []

        async def handle(self, command: RuntimeArtifactEventCommand) -> None:
            self.commands.append(command)

    command = _command()
    spy = SpyHandler()
    worker = RuntimeWorker.__new__(RuntimeWorker)
    worker.artifact_event_handler = spy
    claim = RuntimeWorkerClaim(
        command_id=EVENT_ID,
        command_type=(PersistenceValues.EventType.ARTIFACT_EVENT_PUBLISH_REQUESTED),
        org_id=ORG,
        run_id=RUN,
        locked_by="worker_1",
        lock_expires_at=CREATED_AT,
        payload=command.model_dump(mode="json"),
    )

    await worker._dispatch(claim)

    assert spy.commands == [command]
