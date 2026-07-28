from __future__ import annotations

import pytest

from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.evaluation_repository import (
    InMemoryEvaluationRepository,
)
from runtime_worker.evaluation_projection_composition import (
    build_evaluation_projection,
)


def _settings(**overrides: str) -> RuntimeSettings:
    environ = {
        "RUNTIME_ENVIRONMENT": "development",
        "RUNTIME_EVALUATION_PROJECTION_ENABLED": "true",
        "RUNTIME_EVALUATION_USER_CONSENTED": "true",
        "RUNTIME_EVALUATION_ALLOW_DEVELOPMENT_RUNS": "true",
        **overrides,
    }
    return RuntimeSettings.load(environ=environ)


def test_projection_composition_is_dark_by_default() -> None:
    runtime = InMemoryRuntimeApiStore()

    assert (
        build_evaluation_projection(
            settings=RuntimeSettings.load(environ={}),
            repository=InMemoryEvaluationRepository(),
            event_store=runtime,
            worker_id="worker-1",
        )
        is None
    )


def test_projection_composition_requires_repository_when_policy_permits() -> None:
    with pytest.raises(
        RuntimeError,
        match="no durable repository",
    ):
        build_evaluation_projection(
            settings=_settings(),
            repository=None,
            event_store=InMemoryRuntimeApiStore(),
            worker_id="worker-1",
        )


def test_projection_composition_builds_both_durable_queue_sides() -> None:
    composition = build_evaluation_projection(
        settings=_settings(
            RUNTIME_EVALUATION_MAX_EVENTS_PER_RUN="101",
            RUNTIME_EVALUATION_MAX_PROJECTION_ATTEMPTS="2",
            RUNTIME_EVALUATION_PROJECTION_LEASE_SECONDS="11",
            RUNTIME_EVALUATION_PROJECTION_CLAIM_BATCH="3",
        ),
        repository=InMemoryEvaluationRepository(),
        event_store=InMemoryRuntimeApiStore(),
        worker_id="worker-1",
    )

    assert composition is not None
    assert composition.observer._limits.max_events_per_run == 101
    assert composition.runner._limits.max_attempts == 2
    assert composition.runner._limits.lease_seconds == 11
    assert composition.runner._limits.claim_batch_size == 3
