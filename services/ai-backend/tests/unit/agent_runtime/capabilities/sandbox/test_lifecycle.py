"""Durability and no-blind-retry invariants for sandbox lifecycle state."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.sandbox.contracts import (
    SandboxLifecycleRecord,
    SandboxLifecycleState,
)
from agent_runtime.capabilities.sandbox.lifecycle import (
    FileSandboxLifecycleStore,
    InMemorySandboxLifecycleStore,
    SandboxLifecycleConflict,
    SandboxLifecycleTransitionError,
)


def _record(
    *,
    idempotency_key: str = "sandbox:run-1:operation-1",
    request_digest: str = "a" * 64,
) -> SandboxLifecycleRecord:
    return SandboxLifecycleRecord(
        operation_id="operation-1",
        run_id="run-1",
        idempotency_key=idempotency_key,
        request_digest=request_digest,
    )


class TestLifecycleRecord:
    def test_active_or_indeterminate_execution_requires_started_fact(self) -> None:
        for state in (
            SandboxLifecycleState.RUNNING,
            SandboxLifecycleState.COLLECTING,
            SandboxLifecycleState.INDETERMINATE,
        ):
            with pytest.raises(ValidationError):
                SandboxLifecycleRecord(
                    operation_id="operation-1",
                    run_id="run-1",
                    idempotency_key="x",
                    request_digest="a" * 64,
                    state=state,
                )

    def test_transition_is_an_immutable_value(self) -> None:
        requested = _record()
        provisioned = requested.transition(
            state=SandboxLifecycleState.PROVISIONED,
            provider_session_ref="provider-session-opaque-1",
        )

        assert requested.state is SandboxLifecycleState.REQUESTED
        assert provisioned.state is SandboxLifecycleState.PROVISIONED
        assert provisioned.provider_session_ref == "provider-session-opaque-1"


@pytest.mark.asyncio
class TestInMemorySandboxLifecycleStore:
    async def test_acquire_is_idempotent_and_rejects_identity_reuse(self) -> None:
        store = InMemorySandboxLifecycleStore()
        record = _record()

        first = await store.acquire(record=record)
        repeat = await store.acquire(record=record)

        assert first.created is True
        assert repeat.created is False
        assert repeat.record == record

        with pytest.raises(SandboxLifecycleConflict):
            await store.acquire(record=_record(request_digest="b" * 64))

    async def test_rejects_backward_execution_fact_and_provider_ref_replacement(
        self,
    ) -> None:
        store = InMemorySandboxLifecycleStore()
        requested = _record()
        await store.acquire(record=requested)
        provisioned = requested.transition(
            state=SandboxLifecycleState.PROVISIONED,
            provider_session_ref="provider-session-opaque-1",
        )
        await store.update(record=provisioned)
        uploading = provisioned.transition(state=SandboxLifecycleState.UPLOADING)
        await store.update(record=uploading)
        running = uploading.transition(
            state=SandboxLifecycleState.RUNNING,
            execution_started=True,
        )
        await store.update(record=running)

        with pytest.raises(SandboxLifecycleTransitionError):
            await store.update(
                record=running.transition(
                    state=SandboxLifecycleState.COLLECTING,
                    execution_started=False,
                )
            )
        with pytest.raises(SandboxLifecycleTransitionError):
            await store.update(
                record=running.transition(
                    state=SandboxLifecycleState.COLLECTING,
                    provider_session_ref="provider-session-opaque-2",
                )
            )

    async def test_rejects_invalid_transition_and_lists_only_recoverable(self) -> None:
        store = InMemorySandboxLifecycleStore()
        requested = _record()
        await store.acquire(record=requested)
        provisioned = requested.transition(state=SandboxLifecycleState.PROVISIONED)
        await store.update(record=provisioned)

        with pytest.raises(SandboxLifecycleTransitionError):
            await store.update(
                record=provisioned.transition(state=SandboxLifecycleState.REQUESTED)
            )

        cleanup = provisioned.transition(state=SandboxLifecycleState.CLEANUP_PENDING)
        await store.update(record=cleanup)
        cleaned = cleanup.transition(state=SandboxLifecycleState.CLEANED)
        await store.update(record=cleaned)

        assert await store.list_recoverable() == ()


@pytest.mark.asyncio
class TestFileSandboxLifecycleStore:
    async def test_persists_across_instances_and_recovers_unresolved_record(
        self, tmp_path: Path
    ) -> None:
        record = _record()
        store = FileSandboxLifecycleStore(root=tmp_path / "lifecycle")
        assert (await store.acquire(record=record)).created is True
        provisioned = record.transition(state=SandboxLifecycleState.PROVISIONED)
        assert await store.update(record=provisioned) == provisioned

        reopened = FileSandboxLifecycleStore(root=tmp_path / "lifecycle")
        assert await reopened.get(idempotency_key=record.idempotency_key) == provisioned
        assert await reopened.list_recoverable() == (provisioned,)

    async def test_concurrent_acquire_creates_exactly_once(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "lifecycle"
        record = _record()

        results = await asyncio.gather(
            *(
                FileSandboxLifecycleStore(root=root).acquire(record=record)
                for _ in range(8)
            )
        )

        assert sum(result.created for result in results) == 1
        assert {result.record for result in results} == {record}

    async def test_rejects_symlinked_record_file(self, tmp_path: Path) -> None:
        store = FileSandboxLifecycleStore(root=tmp_path / "lifecycle")
        record = _record()
        path = store._record_path(record.idempotency_key)
        path.symlink_to(tmp_path / "outside.json")

        with pytest.raises(SandboxLifecycleTransitionError):
            await store.get(idempotency_key=record.idempotency_key)
