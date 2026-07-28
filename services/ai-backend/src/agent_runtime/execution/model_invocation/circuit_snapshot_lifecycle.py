"""Optional desktop lifecycle composition for process-local circuit health."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from agent_runtime.execution.model_invocation.circuit_health import (
    ProcessLocalProviderCircuitHealth,
)
from runtime_adapters.file.provider_circuit_snapshot import (
    CircuitSnapshotProfile,
    DesktopProviderCircuitSnapshotStore,
)


class DesktopProviderCircuitSnapshotLifecycle:
    """Restore once and atomically flush at a worker/app lifecycle boundary.

    This is deliberately an optional process-local convenience.  It is active
    only for the single-user desktop profile with a configured runtime data
    root; web and multi-worker deployments retain memory-only circuit state.
    """

    _ENV_ENABLED = "RUNTIME_PROVIDER_CIRCUIT_SNAPSHOT_ENABLED"
    _ENV_ROOT = "RUNTIME_FILE_STORE_ROOT"
    _ENV_PROFILE = "ENTERPRISE_DEPLOYMENT_PROFILE"
    _FILE_NAME = "provider-circuit-health.v1.json"

    def __init__(self, store: DesktopProviderCircuitSnapshotStore) -> None:
        self._store = store

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> "DesktopProviderCircuitSnapshotLifecycle | None":
        values = os.environ if environ is None else environ
        if values.get(cls._ENV_ENABLED, "").strip().lower() not in {
            "1",
            "true",
            "yes",
        }:
            return None
        if values.get(cls._ENV_PROFILE, "").strip() != "single_user_desktop":
            return None
        root = values.get(cls._ENV_ROOT, "").strip()
        if not root:
            return None
        return cls(
            DesktopProviderCircuitSnapshotStore(
                Path(root) / "runtime-health" / cls._FILE_NAME,
                profile=CircuitSnapshotProfile.SINGLE_USER_DESKTOP,
            )
        )

    def restore(self, health: ProcessLocalProviderCircuitHealth) -> bool:
        snapshot = self._store.load()
        if snapshot is None:
            return False
        health.restore(snapshot)
        return True

    def flush(self, health: ProcessLocalProviderCircuitHealth) -> None:
        """Durably replace the bounded snapshot; capacity failures stay visible."""

        self._store.save(health.snapshot())


__all__ = ("DesktopProviderCircuitSnapshotLifecycle",)
