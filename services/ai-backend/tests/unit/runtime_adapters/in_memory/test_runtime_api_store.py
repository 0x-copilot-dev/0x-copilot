from __future__ import annotations

from datetime import UTC, datetime, timedelta

from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import RuntimeRunCommand
from agent_runtime.persistence.records import RuntimeWorkerResult


def test_in_memory_runtime_queue_claim_retry_and_dead_letter() -> None:
    store = InMemoryRuntimeApiStore()
    command = RuntimeRunCommand(
        run_id="run_123",
        conversation_id="conversation_123",
        org_id="org_123",
        user_id="user_123",
        trace_id="trace_123",
        runtime_context={
            "user_id": "user_123",
            "org_id": "org_123",
            "roles": ["employee"],
            "permission_scopes": ["docs:read"],
            "connector_scopes": {},
            "model_profile": {
                "provider": "fake",
                "model_name": "fake-enterprise-model",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
            "request_id": "request_123",
            "run_id": "run_123",
            "trace_id": "trace_123",
        },
    )

    store.enqueue_run(command)
    first_claim = store.claim_next(
        worker_id="worker_1",
        lock_expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )

    assert first_claim is not None
    assert first_claim.run_id == "run_123"
    assert store.claim_next(
        worker_id="worker_2",
        lock_expires_at=datetime.now(UTC) + timedelta(seconds=30),
    ) is None

    store.mark_retry(
        result=RuntimeWorkerResult(
            command_id=first_claim.command_id,
            succeeded=False,
            retry_available_at=datetime.now(UTC),
        )
    )
    retry_claim = store.claim_next(
        worker_id="worker_2",
        lock_expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )

    assert retry_claim is not None
    assert retry_claim.attempts == 2

    store.mark_dead_letter(
        result=RuntimeWorkerResult(command_id=retry_claim.command_id, succeeded=False)
    )
    assert store.claim_next(
        worker_id="worker_3",
        lock_expires_at=datetime.now(UTC) + timedelta(seconds=30),
    ) is None
