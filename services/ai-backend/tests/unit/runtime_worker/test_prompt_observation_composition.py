"""Production worker composition for canonical F2 prompt observations."""

from __future__ import annotations

from agent_runtime.api.prompt_observation_store import (
    EventJournalPromptObservationStore,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_worker.loop import RuntimeWorker


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


def test_runtime_ports_observer_is_shared_by_run_and_resume_handlers() -> None:
    ports = RuntimeAdapterFactory.from_store(InMemoryRuntimeApiStore())
    worker = RuntimeWorker(
        persistence=ports.persistence,
        event_store=ports.event_store,
        queue=ports.queue,
        settings=_settings(),
        run_control_snapshot_store=ports.run_control_snapshot_store,
        prompt_observation_store=ports.prompt_observation_store,
    )

    assert worker.prompt_observation_store is ports.prompt_observation_store
    assert (
        worker.run_handler._prompt_observation_store  # noqa: SLF001
        is ports.prompt_observation_store
    )
    assert (
        worker.approval_handler._prompt_observation_store  # noqa: SLF001
        is ports.prompt_observation_store
    )


def test_public_snapshot_store_injection_builds_canonical_observer_store() -> None:
    ports = RuntimeAdapterFactory.from_store(InMemoryRuntimeApiStore())
    worker = RuntimeWorker(
        persistence=ports.persistence,
        event_store=ports.event_store,
        queue=ports.queue,
        settings=_settings(),
        run_control_snapshot_store=ports.run_control_snapshot_store,
    )

    assert isinstance(
        worker.prompt_observation_store,
        EventJournalPromptObservationStore,
    )
