"""Backend selection for the physical-artifact cleanup scheduler state."""

from __future__ import annotations

from agent_runtime.artifacts.cleanup_schedule import ArtifactCleanupScheduleStore
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.settings import RuntimeSettings


def build_artifact_cleanup_schedule_store(
    *, settings: RuntimeSettings, persistence: object
) -> ArtifactCleanupScheduleStore:
    """Return the backend-correct durable cursor/lease store.

    The returned port owns scheduler metadata only.  It cannot enumerate or
    mutate artifact content, references, or legal holds.
    """

    backend = settings.store.backend
    if backend == "file":
        root = settings.store.file_store_root
        if not root:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "RUNTIME_FILE_STORE_ROOT is required for artifact cleanup scheduling.",
                retryable=False,
            )
        from runtime_adapters.file.artifact_cleanup_schedule_store import (
            FileArtifactCleanupScheduleStore,
        )

        return FileArtifactCleanupScheduleStore(root=root)
    from runtime_adapters.in_memory.artifact_cleanup_schedule_store import (
        InMemoryArtifactCleanupScheduleStore,
    )

    return InMemoryArtifactCleanupScheduleStore()


__all__ = ("build_artifact_cleanup_schedule_store",)
