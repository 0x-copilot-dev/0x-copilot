from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory.evaluation_repository import (
    InMemoryEvaluationRepository,
)
from runtime_worker.run_control import RunControlAssignment, RunControlPlaneBuilder
from runtime_worker.run_control_release_composition import (
    RunControlReleaseCompositionError,
    build_local_release_control_service,
    build_run_control_plane_builder,
)

from tests.unit.runtime_worker.test_run_control import _SnapshotStore


_ENVIRONMENT = {
    "RUNTIME_ENVIRONMENT": "development",
    "ENTERPRISE_DEPLOYMENT_PROFILE": "single_user_desktop",
    "ENTERPRISE_SERVICE_TOKEN": "local-control-service-token",
}


def _release_config(path, *, profile: str = "development") -> None:
    public_key = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(
            Encoding.Raw,
            PublicFormat.Raw,
        )
    )
    assignment = RunControlAssignment.safe_active_v1()
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "release_profile": profile,
                "verification_keys": [
                    {
                        "key_id": "release-key-v1",
                        "public_key_b64": base64.b64encode(public_key).decode("ascii"),
                    }
                ],
                "assignments": [assignment.model_dump(mode="json")],
                "development_override": None,
            }
        ),
        encoding="utf-8",
    )


async def test_no_active_release_uses_safe_builder_without_configuration() -> None:
    builder = await build_run_control_plane_builder(
        settings=RuntimeSettings.load(environ=_ENVIRONMENT),
        repository=InMemoryEvaluationRepository(),
        store=_SnapshotStore(),
        environment=_ENVIRONMENT,
    )

    assert isinstance(builder, RunControlPlaneBuilder)


async def test_task_policy_release_composition_requires_complete_journal_seam() -> None:
    with pytest.raises(
        RunControlReleaseCompositionError,
        match="factory and durable journal callbacks",
    ):
        await build_run_control_plane_builder(
            settings=RuntimeSettings.load(environ=_ENVIRONMENT),
            repository=InMemoryEvaluationRepository(),
            store=_SnapshotStore(),
            environment=_ENVIRONMENT,
            task_policy_runtime_factory=object(),  # type: ignore[arg-type]
        )


async def test_release_configuration_requires_repository() -> None:
    settings = RuntimeSettings.load(
        environ={
            **_ENVIRONMENT,
            "RUNTIME_HARNESS_RELEASE_CONFIG_PATH": "/tmp/release.json",
        }
    )
    with pytest.raises(
        RunControlReleaseCompositionError,
        match="requires an evaluation repository",
    ):
        await build_run_control_plane_builder(
            settings=settings,
            repository=None,
            store=_SnapshotStore(),
            environment=_ENVIRONMENT,
        )


async def test_active_pointer_without_verification_configuration_fails_closed() -> None:
    class _ActiveRepository:
        async def get_active_harness_manifest(self, _scope):
            return object()

    with pytest.raises(
        RunControlReleaseCompositionError,
        match="no verification configuration",
    ):
        await build_run_control_plane_builder(
            settings=RuntimeSettings.load(environ=_ENVIRONMENT),
            repository=_ActiveRepository(),  # type: ignore[arg-type]
            store=_SnapshotStore(),
            environment=_ENVIRONMENT,
        )


def test_local_control_composes_only_from_explicit_nonproduction_profile(
    tmp_path,
) -> None:
    path = tmp_path / "release.json"
    _release_config(path)
    settings = RuntimeSettings.load(
        environ={
            **_ENVIRONMENT,
            "RUNTIME_HARNESS_RELEASE_CONFIG_PATH": str(path),
            "RUNTIME_LOCAL_RELEASE_CONTROL_ENABLED": "true",
        }
    )

    service = build_local_release_control_service(
        settings=settings,
        repository=InMemoryEvaluationRepository(),
        environment=_ENVIRONMENT,
    )

    assert service is not None


def test_production_runtime_rejects_development_release_profile(tmp_path) -> None:
    path = tmp_path / "release.json"
    _release_config(path)
    settings = RuntimeSettings.load(
        environ={
            "RUNTIME_ENVIRONMENT": "production",
            "RUNTIME_HARNESS_RELEASE_CONFIG_PATH": str(path),
        }
    )

    with pytest.raises(
        RunControlReleaseCompositionError,
        match="requires a production release profile",
    ):
        build_local_release_control_service(
            settings=settings.model_copy(
                update={
                    "evaluation": settings.evaluation.model_copy(
                        update={"local_release_control_enabled": True}
                    )
                }
            ),
            repository=InMemoryEvaluationRepository(),
            environment={
                "RUNTIME_ENVIRONMENT": "production",
                "ENTERPRISE_SERVICE_TOKEN": "production-service-token",
            },
        )


def test_local_control_rejects_missing_service_token(tmp_path) -> None:
    path = tmp_path / "release.json"
    _release_config(path)
    settings = RuntimeSettings.load(
        environ={
            **_ENVIRONMENT,
            "RUNTIME_HARNESS_RELEASE_CONFIG_PATH": str(path),
            "RUNTIME_LOCAL_RELEASE_CONTROL_ENABLED": "true",
        }
    )

    with pytest.raises(
        RunControlReleaseCompositionError,
        match="requires ENTERPRISE_SERVICE_TOKEN",
    ):
        build_local_release_control_service(
            settings=settings,
            repository=InMemoryEvaluationRepository(),
            environment={},
        )
