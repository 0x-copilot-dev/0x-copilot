from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.execution.errors import AgentRuntimeError
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


def test_postgres_projection_requires_an_explicit_shared_evaluation_root() -> None:
    settings = RuntimeSettings.load(
        environ={
            "RUNTIME_STORE_BACKEND": "postgres",
            "DATABASE_URL": "postgresql://local:local@127.0.0.1/local",
            "RUNTIME_EVALUATION_PROJECTION_ENABLED": "true",
        }
    )

    with pytest.raises(
        AgentRuntimeError,
        match="RUNTIME_EVALUATION_STORE_ROOT",
    ):
        RuntimeAdapterFactory.from_settings(settings)


def test_postgres_can_compose_the_locked_file_cas_evaluation_adapter(
    tmp_path: Path,
) -> None:
    settings = RuntimeSettings.load(
        environ={
            "RUNTIME_STORE_BACKEND": "postgres",
            "DATABASE_URL": "postgresql://local:local@127.0.0.1/local",
            "RUNTIME_EVALUATION_STORE_ROOT": str(tmp_path),
        }
    )

    ports = RuntimeAdapterFactory.from_settings(settings)

    assert isinstance(ports.evaluation_repository, FileEvaluationRepository)
    assert ports.evaluation_repository._object_store._quota.max_bytes == 536_870_912


def test_postgres_evaluation_adapter_uses_its_explicit_quota(tmp_path: Path) -> None:
    settings = RuntimeSettings.load(
        environ={
            "RUNTIME_STORE_BACKEND": "postgres",
            "DATABASE_URL": "postgresql://local:local@127.0.0.1/local",
            "RUNTIME_EVALUATION_STORE_ROOT": str(tmp_path),
            "RUNTIME_EVALUATION_STORE_MAX_BYTES": "4096",
        }
    )

    ports = RuntimeAdapterFactory.from_settings(settings)

    assert isinstance(ports.evaluation_repository, FileEvaluationRepository)
    assert ports.evaluation_repository._object_store._quota.max_bytes == 4096
