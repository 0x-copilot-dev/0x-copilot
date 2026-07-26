"""Remote sandbox execution capability (AC7) — provider-neutral, built-in-first.

Built on the pinned Deep Agents ``SandboxBackendProtocol``. The runtime depends
on product contracts and this package's ports, never on a provider SDK type.

FOUNDATION scope: provider registry + one pinned provider (langsmith, lazy) +
lifecycle (create/execute/teardown, TTL, leak detection) + a policy-enforced
sandbox backend + snapshot/patch validation. DEFERRED to a follow-up: provider
egress-policy compilation/enforcement and host patch-apply (a separate AC5
broker operation).

Gated OFF by default behind ``RUNTIME_ENABLE_REMOTE_SANDBOX``.
"""

from __future__ import annotations

from agent_runtime.capabilities.sandbox.config import (
    RemoteSandboxConfig,
    SandboxLimitProfile,
    SandboxLimitProfiles,
)
from agent_runtime.capabilities.sandbox.contracts import (
    ArtifactRef,
    ManagedSandboxSession,
    SandboxCommandResult,
    SandboxCreateRequest,
    SandboxEgressPolicy,
    SandboxError,
    SandboxErrorCode,
    SandboxIsolationAttestation,
    SandboxLifecycleRecord,
    SandboxLifecycleState,
    SandboxPatchImportRequest,
    SandboxPatchManifest,
    SandboxProviderId,
    SandboxRunRequest,
    SandboxRunResult,
    SandboxSecretLeaseRef,
    SandboxSnapshot,
    SandboxUsageAttribution,
    WorkspacePatchEntry,
    WorkspacePatchManifest,
    WorkspaceTransferEntry,
    WorkspaceTransferManifest,
)
from agent_runtime.capabilities.sandbox.policy_backend import (
    PolicyEnforcedSandboxBackend,
)
from agent_runtime.capabilities.sandbox.ports import (
    SandboxEvent,
    SandboxEventSink,
    SandboxHandle,
    SandboxLifecycleAcquisition,
    SandboxLifecycleStore,
    SandboxPatchCollectorPort,
    SandboxPatchImportPort,
    SandboxProviderPort,
    SandboxRuntimePort,
    SandboxSessionStore,
    SandboxSnapshotContentPort,
    SandboxUsageMeterPort,
)
from agent_runtime.capabilities.sandbox.provider_registry import (
    InMemorySandboxSessionStore,
    SandboxProviderRegistry,
)
from agent_runtime.capabilities.sandbox.lifecycle import (
    FileSandboxLifecycleStore,
    InMemorySandboxLifecycleStore,
    SandboxLifecycleConflict,
    SandboxLifecycleTransitionError,
)
from agent_runtime.capabilities.sandbox.remote_execution_service import (
    ActiveSandbox,
    RemoteExecutionService,
    SandboxEventName,
)
from agent_runtime.capabilities.sandbox.seam import build_sandbox_backend
from agent_runtime.capabilities.sandbox.readiness import (
    SandboxCapabilityReadiness,
    SandboxReadinessReason,
)
from agent_runtime.capabilities.sandbox.coordinator import SandboxLifecycleCoordinator
from agent_runtime.capabilities.sandbox.runtime_adapter import DeepAgentSandboxRuntime
from agent_runtime.capabilities.sandbox.artifact_publisher import (
    ArtifactServiceSandboxPublisher,
)
from agent_runtime.capabilities.sandbox.usage_meter import (
    FileSandboxUsageMeter,
    InMemorySandboxUsageMeter,
)
from agent_runtime.capabilities.sandbox.workspace_transfer import (
    WORKSPACE_ROOT,
    RawSnapshotEntry,
    WorkspaceManifestBuilder,
    WorkspacePatchBuilder,
    WorkspacePathValidator,
)

__all__ = [
    "WORKSPACE_ROOT",
    "ActiveSandbox",
    "ArtifactRef",
    "ArtifactServiceSandboxPublisher",
    "DeepAgentSandboxRuntime",
    "FileSandboxUsageMeter",
    "InMemorySandboxSessionStore",
    "InMemorySandboxLifecycleStore",
    "FileSandboxLifecycleStore",
    "ManagedSandboxSession",
    "PolicyEnforcedSandboxBackend",
    "RawSnapshotEntry",
    "RemoteExecutionService",
    "RemoteSandboxConfig",
    "SandboxCommandResult",
    "SandboxCreateRequest",
    "SandboxEgressPolicy",
    "SandboxError",
    "SandboxErrorCode",
    "SandboxCapabilityReadiness",
    "SandboxReadinessReason",
    "SandboxEvent",
    "SandboxEventName",
    "SandboxEventSink",
    "SandboxHandle",
    "SandboxIsolationAttestation",
    "SandboxLifecycleAcquisition",
    "SandboxLifecycleConflict",
    "SandboxLifecycleRecord",
    "SandboxLifecycleState",
    "SandboxLifecycleStore",
    "SandboxLifecycleCoordinator",
    "SandboxLifecycleTransitionError",
    "SandboxLimitProfile",
    "SandboxLimitProfiles",
    "SandboxProviderId",
    "SandboxProviderPort",
    "SandboxProviderRegistry",
    "SandboxPatchCollectorPort",
    "SandboxPatchImportPort",
    "SandboxPatchImportRequest",
    "SandboxPatchManifest",
    "SandboxRunRequest",
    "SandboxRunResult",
    "SandboxRuntimePort",
    "SandboxSecretLeaseRef",
    "SandboxSessionStore",
    "SandboxSnapshot",
    "SandboxSnapshotContentPort",
    "SandboxUsageAttribution",
    "SandboxUsageMeterPort",
    "WorkspaceManifestBuilder",
    "WorkspacePatchBuilder",
    "WorkspacePatchEntry",
    "WorkspacePatchManifest",
    "WorkspacePathValidator",
    "WorkspaceTransferEntry",
    "WorkspaceTransferManifest",
    "InMemorySandboxUsageMeter",
    "build_sandbox_backend",
]
