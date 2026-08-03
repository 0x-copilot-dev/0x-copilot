from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.harness_quality.ports import EvaluationObjectDeletionPolicy
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.file.evaluation_repository import FileEvaluationRepository
from runtime_adapters.in_memory.evaluation_repository import (
    InMemoryEvaluationRepository,
)


def test_in_memory_runtime_composes_only_the_hermetic_evaluation_adapter() -> None:
    ports = RuntimeAdapterFactory.from_settings(
        RuntimeSettings.load(environ={"RUNTIME_STORE_BACKEND": "in_memory"})
    )

    assert isinstance(ports.evaluation_repository, InMemoryEvaluationRepository)


def test_desktop_file_runtime_composes_evaluation_on_the_existing_file_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENTERPRISE_DEPLOYMENT_PROFILE", "single_user_desktop")
    settings = RuntimeSettings.load(
        environ={
            "RUNTIME_STORE_BACKEND": "file",
            "RUNTIME_FILE_STORE_ROOT": str(tmp_path),
        }
    )

    ports = RuntimeAdapterFactory.from_settings(settings)

    assert isinstance(ports.evaluation_repository, FileEvaluationRepository)
    assert ports.evaluation_repository._object_store is ports.persistence.object_store
    assert (
        ports.evaluation_repository
        in ports.persistence._external_object_reference_providers
    )
    assert (
        ports.evaluation_repository in ports.persistence._source_run_deletion_observers
    )
    assert (
        ports.evaluation_repository._object_deletion_policy
        is EvaluationObjectDeletionPolicy.SHARED_STORE_METADATA_ONLY
    )
