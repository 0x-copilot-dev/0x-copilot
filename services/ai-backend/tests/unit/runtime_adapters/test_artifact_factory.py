"""Fail-closed settings and composition for A2 artifact persistence."""

from __future__ import annotations

from dataclasses import replace

import pytest

from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.artifact_blob_store import (
    InMemoryArtifactBlobStore,
)
from runtime_adapters.in_memory.artifact_metadata_store import (
    InMemoryArtifactMetadataStore,
)


def _blob_root_settings(
    *, blob_root: str | None, enabled: bool = True
) -> RuntimeSettings:
    environ = {
        "OPENAI_API_KEY": "sk-test",
        "RUNTIME_STORE_BACKEND": "in_memory",
        "ARTIFACT_EFFECTS_V2": "true" if enabled else "false",
    }
    if blob_root is not None:
        environ["RUNTIME_ARTIFACT_BLOB_ROOT"] = blob_root
    return RuntimeSettings.load(environ=environ)


class TestArtifactFactory:
    def test_settings_load_explicit_blob_root(self, tmp_path) -> None:
        root = tmp_path / "shared-artifacts"
        settings = _blob_root_settings(blob_root=str(root))

        assert settings.store.artifact_blob_root == str(root)

    def test_in_memory_and_from_store_compose_only_when_enabled(self) -> None:
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_STORE_BACKEND": "in_memory",
                "ARTIFACT_EFFECTS_V2": "true",
            }
        )

        settings_ports = RuntimeAdapterFactory.from_settings(settings)
        store_ports = RuntimeAdapterFactory.from_store(
            InMemoryRuntimeApiStore(),
            artifact_effects_v2=True,
        )

        for ports in (settings_ports, store_ports):
            assert isinstance(
                ports.artifact_metadata_store,
                InMemoryArtifactMetadataStore,
            )
            assert isinstance(ports.artifact_blob_store, InMemoryArtifactBlobStore)
            assert ports.artifact_repository is not None
            assert (
                ports.artifact_metadata_store.coordinator
                is ports.artifact_blob_store.coordinator
            )
            dependencies = ports.require_artifact_service_storage()
            assert dependencies is not None
            assert dependencies.metadata_store is ports.artifact_metadata_store
            assert dependencies.blob_store is ports.artifact_blob_store
            assert dependencies.event_publication is ports.queue

        legacy = RuntimeAdapterFactory.from_store(InMemoryRuntimeApiStore())
        assert legacy.artifact_effects_v2 is False
        assert legacy.artifact_metadata_store is None
        assert legacy.require_artifact_service_storage() is None

    def test_enabled_incomplete_composition_fails_during_construction(self) -> None:
        complete = RuntimeAdapterFactory.from_store(
            InMemoryRuntimeApiStore(),
            artifact_effects_v2=True,
        )

        with pytest.raises(AgentRuntimeError) as captured:
            replace(complete, artifact_blob_store=None)

        assert captured.value.code is RuntimeErrorCode.CONFIGURATION_ERROR
        assert "missing blob" in captured.value.safe_message
