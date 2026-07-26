"""Contract tests for body-free A5 reconciliation queue envelopes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from agent_runtime.persistence.constants import Values as PersistenceValues
from agent_runtime.persistence.records import RuntimeWorkerResult
from runtime_adapters.artifact_queue import ArtifactAwareRuntimeQueue
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_adapters.postgres.runtime_api_store import PostgresRuntimeApiStore
from runtime_api.schemas import RuntimeEffectReconcileCommand

pytestmark = pytest.mark.anyio

_CREATED_AT = datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc)
_FORBIDDEN_SCOPE_KEYS = {"user_id", "conversation_id", "stage_id"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _command() -> RuntimeEffectReconcileCommand:
    return RuntimeEffectReconcileCommand(
        command_id="effect-reconcile-contract-1",
        org_id="org_acme",
        run_id="run_1",
        claim_id="clm_abc123",
        created_at=_CREATED_AT,
    )


def test_reconcile_command_accepts_only_durable_recovery_scope() -> None:
    command = _command()

    assert command.model_dump(mode="json") == {
        "command_id": "effect-reconcile-contract-1",
        "org_id": "org_acme",
        "run_id": "run_1",
        "claim_id": "clm_abc123",
        "trace_propagation": {},
        "created_at": "2026-07-25T08:00:00Z",
    }

    with pytest.raises(ValidationError):
        RuntimeEffectReconcileCommand.model_validate(
            {
                **command.model_dump(mode="json"),
                "user_id": "user_sarah",
            }
        )


@pytest.mark.parametrize("adapter", ("memory", "file"))
async def test_queue_adapters_persist_only_body_free_recovery_payload(
    adapter: str,
    tmp_path,
) -> None:
    command = _command()
    store: Any
    if adapter == "memory":
        store = InMemoryRuntimeApiStore()
    else:
        store = FileRuntimeApiStore(tmp_path / "runtime")

    assert await store.enqueue_effect_reconcile(command) is True
    claimed = await store.claim_next(
        worker_id="worker_1",
        lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )

    assert claimed is not None
    assert (
        claimed.command_type == PersistenceValues.EventType.EFFECT_RECONCILE_REQUESTED
    )
    assert claimed.org_id == command.org_id
    assert claimed.run_id == command.run_id
    assert _FORBIDDEN_SCOPE_KEYS.isdisjoint(claimed.payload)
    assert claimed.payload["claim_id"] == command.claim_id
    await store.mark_complete(
        result=RuntimeWorkerResult(command_id=command.command_id, succeeded=True)
    )


class _PostgresEnqueueSpy:
    captured: dict[str, object] | None = None

    async def _enqueue_command(self, **kwargs: object) -> bool:
        self.captured = kwargs
        return True


async def test_postgres_queue_adapter_serializes_only_body_free_recovery_payload() -> (
    None
):
    command = _command()
    spy = _PostgresEnqueueSpy()

    assert (  # type: ignore[arg-type]
        await PostgresRuntimeApiStore.enqueue_effect_reconcile(spy, command)
    ) is True

    assert spy.captured is not None
    assert spy.captured["command_type"] == (
        PersistenceValues.EventType.EFFECT_RECONCILE_REQUESTED
    )
    assert spy.captured["org_id"] == command.org_id
    assert spy.captured["aggregate_id"] == command.run_id
    assert spy.captured["idempotent"] is True
    payload = spy.captured["payload"]
    assert isinstance(payload, dict)
    assert _FORBIDDEN_SCOPE_KEYS.isdisjoint(payload)
    assert payload == command.model_dump(mode="json")


class _ArtifactQueueMirror:
    def __init__(self) -> None:
        self.reconcile_commands: list[RuntimeEffectReconcileCommand] = []

    async def enqueue_artifact_event(self, command: object) -> None:
        del command

    async def artifact_event_status(self, *, event_id: str) -> object | None:
        del event_id
        return None

    async def enqueue_effect_reconcile(
        self, command: RuntimeEffectReconcileCommand
    ) -> bool:
        self.reconcile_commands.append(command)
        return True


class _ArtifactCanonicalOutbox:
    async def pending_artifact_events(self) -> tuple[object, ...]:
        return ()

    async def acknowledge_artifact_event(
        self, *, event_id: str, status: object
    ) -> None:
        del event_id, status


async def test_artifact_aware_queue_forwards_body_free_recovery_command() -> None:
    command = _command()
    mirror = _ArtifactQueueMirror()
    queue = ArtifactAwareRuntimeQueue(mirror, _ArtifactCanonicalOutbox())

    assert await queue.enqueue_effect_reconcile(command) is True

    assert mirror.reconcile_commands == [command]
    payload = mirror.reconcile_commands[0].model_dump(mode="json")
    assert _FORBIDDEN_SCOPE_KEYS.isdisjoint(payload)


@pytest.mark.parametrize("adapter", ("memory", "file"))
async def test_reconcile_queue_idempotently_absorbs_an_exact_recovery_replay(
    adapter: str,
    tmp_path,
) -> None:
    command = _command()
    store: Any
    if adapter == "memory":
        store = InMemoryRuntimeApiStore()
    else:
        store = FileRuntimeApiStore(tmp_path / "runtime")

    assert await store.enqueue_effect_reconcile(command) is True
    assert await store.enqueue_effect_reconcile(command) is False
    claimed = await store.claim_next(
        worker_id="worker_1",
        lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )

    assert claimed is not None
    assert claimed.command_id == command.command_id
    assert (
        await store.claim_next(
            worker_id="worker_2",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        )
        is None
    )


@pytest.mark.parametrize("adapter", ("memory", "file"))
async def test_reconcile_queue_rejects_a_conflicting_command_id(
    adapter: str,
    tmp_path,
) -> None:
    command = _command()
    conflicting = command.model_copy(update={"run_id": "run_conflicting"})
    store: Any
    if adapter == "memory":
        store = InMemoryRuntimeApiStore()
    else:
        store = FileRuntimeApiStore(tmp_path / "runtime")

    assert await store.enqueue_effect_reconcile(command) is True
    with pytest.raises(ValueError, match="command id conflicts"):
        await store.enqueue_effect_reconcile(conflicting)
