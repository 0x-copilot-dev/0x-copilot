from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_runtime.execution.model_invocation.circuit_health import (
    ProcessLocalProviderCircuitHealth,
    ProviderCircuitConfig,
    ProviderCircuitKey,
)
from agent_runtime.execution.model_invocation.circuit_snapshot_lifecycle import (
    DesktopProviderCircuitSnapshotLifecycle,
)
from agent_runtime.execution.model_invocation.contracts import (
    ModelCredentialMode,
    ModelDeploymentHealth,
    ModelFailureClass,
)
from runtime_adapters.file.provider_circuit_snapshot import (
    CircuitSnapshotProfile,
    DesktopProviderCircuitSnapshotStore,
)


def _key() -> ProviderCircuitKey:
    return ProviderCircuitKey(
        provider="openai",
        deployment_id="primary",
        region="us-east",
        credential_mode=ModelCredentialMode.DEPLOYMENT,
    )


def test_atomic_snapshot_supports_process_restart(tmp_path: Path) -> None:
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    health = ProcessLocalProviderCircuitHealth(
        ProviderCircuitConfig(open_failure_threshold=1), now=lambda: now
    )
    health.observe_failure(_key(), ModelFailureClass.PROVIDER_OVERLOADED)
    store = DesktopProviderCircuitSnapshotStore(
        tmp_path / "provider-health.json",
        profile=CircuitSnapshotProfile.SINGLE_USER_DESKTOP,
    )
    store.save(health.snapshot())

    restarted = ProcessLocalProviderCircuitHealth(
        ProviderCircuitConfig(open_failure_threshold=1), now=lambda: now
    )
    loaded = store.load()
    assert loaded is not None
    restarted.restore(loaded)
    assert restarted.health(_key()) is ModelDeploymentHealth.OPEN_CIRCUIT
    assert not tuple(tmp_path.glob("*.tmp"))


def test_corrupt_or_tampered_snapshot_restores_no_circuit_state(tmp_path: Path) -> None:
    path = tmp_path / "provider-health.json"
    store = DesktopProviderCircuitSnapshotStore(
        path, profile=CircuitSnapshotProfile.SINGLE_USER_DESKTOP
    )
    path.write_text('{"schema_version":"provider-circuit-file.v1","payload":', "utf-8")
    assert store.load() is None
    path.write_text(
        '{"schema_version":"provider-circuit-file.v1","payload":'
        '{"schema_version":"provider-circuit-snapshot.v1",'
        '"captured_at":"2026-07-28T00:00:00Z","entries":[]},'
        '"payload_digest":"sha256:' + ("0" * 64) + '"}',
        "utf-8",
    )
    assert store.load() is None


def test_oversized_snapshot_and_non_desktop_environment_restore_nothing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider-health.json"
    store = DesktopProviderCircuitSnapshotStore(
        path,
        profile=CircuitSnapshotProfile.SINGLE_USER_DESKTOP,
        max_bytes=1024,
    )
    path.write_bytes(b"x" * 1025)
    assert store.load() is None

    assert (
        DesktopProviderCircuitSnapshotLifecycle.from_environment(
            {
                "RUNTIME_PROVIDER_CIRCUIT_SNAPSHOT_ENABLED": "true",
                "ENTERPRISE_DEPLOYMENT_PROFILE": "production",
                "RUNTIME_FILE_STORE_ROOT": str(tmp_path),
            }
        )
        is None
    )


def test_desktop_lifecycle_restores_and_flushes_under_runtime_data_root(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    lifecycle = DesktopProviderCircuitSnapshotLifecycle.from_environment(
        {
            "RUNTIME_PROVIDER_CIRCUIT_SNAPSHOT_ENABLED": "true",
            "ENTERPRISE_DEPLOYMENT_PROFILE": "single_user_desktop",
            "RUNTIME_FILE_STORE_ROOT": str(tmp_path),
        }
    )
    assert lifecycle is not None
    health = ProcessLocalProviderCircuitHealth(
        ProviderCircuitConfig(open_failure_threshold=1), now=lambda: now
    )
    health.observe_failure(_key(), ModelFailureClass.PROVIDER_OVERLOADED)
    lifecycle.flush(health)

    restored = ProcessLocalProviderCircuitHealth(
        ProviderCircuitConfig(open_failure_threshold=1), now=lambda: now
    )
    assert lifecycle.restore(restored)
    assert restored.health(_key()) is ModelDeploymentHealth.OPEN_CIRCUIT
    snapshot_text = (
        tmp_path / "runtime-health" / "provider-circuit-health.v1.json"
    ).read_text()
    assert "sk-live-" not in snapshot_text
    assert "api_key" not in snapshot_text
