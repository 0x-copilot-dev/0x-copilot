"""F10 journal-port composition stays on the canonical event/snapshot stores."""

from __future__ import annotations

import pytest
from copilot_service_contracts.deployment_profile import (
    ENV_DEPLOYMENT_PROFILE,
    PROFILE_SINGLE_USER_DESKTOP,
)

from agent_runtime.api.model_invocation_store import EventJournalModelInvocationStore
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore


def _settings(**overrides: str) -> RuntimeSettings:
    environ = {
        "OPENAI_API_KEY": "test-key",
        "RUNTIME_DEFAULT_PROVIDER": "openai",
        "RUNTIME_DEFAULT_MODEL": "gpt-5-mini",
    }
    environ.update(overrides)
    return RuntimeSettings.load(environ=environ)


def _assert_canonical_journal(ports: object) -> None:
    journal = ports.model_invocation_store
    assert isinstance(journal, EventJournalModelInvocationStore)
    assert journal._events is ports.event_store
    assert journal._snapshots is ports.run_control_snapshot_store


def test_in_memory_ports_expose_the_event_journal_model_invocation_store() -> None:
    _assert_canonical_journal(
        RuntimeAdapterFactory.from_store(InMemoryRuntimeApiStore())
    )
    _assert_canonical_journal(
        RuntimeAdapterFactory.from_settings(
            _settings(RUNTIME_STORE_BACKEND="in_memory")
        )
    )


def test_file_ports_reuse_the_file_event_and_snapshot_stores(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_DEPLOYMENT_PROFILE, PROFILE_SINGLE_USER_DESKTOP)
    _assert_canonical_journal(
        RuntimeAdapterFactory.from_settings(
            _settings(
                RUNTIME_STORE_BACKEND="file",
                RUNTIME_FILE_STORE_ROOT=str(tmp_path / "agent-data"),
            )
        )
    )


def test_postgres_ports_reuse_the_postgres_event_and_snapshot_stores() -> None:
    _assert_canonical_journal(
        RuntimeAdapterFactory.from_settings(
            _settings(
                RUNTIME_STORE_BACKEND="postgres",
                DATABASE_URL="postgresql://user:password@127.0.0.1:5432/runtime",
            )
        )
    )
