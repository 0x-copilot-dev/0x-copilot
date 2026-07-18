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
    SandboxProviderId,
    SandboxSecretLeaseRef,
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
    SandboxProviderPort,
    SandboxSessionStore,
)
from agent_runtime.capabilities.sandbox.provider_registry import (
    InMemorySandboxSessionStore,
    SandboxProviderRegistry,
)
from agent_runtime.capabilities.sandbox.remote_execution_service import (
    ActiveSandbox,
    RemoteExecutionService,
    SandboxEventName,
)
from agent_runtime.capabilities.sandbox.seam import build_sandbox_backend
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
    "InMemorySandboxSessionStore",
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
    "SandboxEvent",
    "SandboxEventName",
    "SandboxEventSink",
    "SandboxHandle",
    "SandboxLimitProfile",
    "SandboxLimitProfiles",
    "SandboxProviderId",
    "SandboxProviderPort",
    "SandboxProviderRegistry",
    "SandboxSecretLeaseRef",
    "SandboxSessionStore",
    "WorkspaceManifestBuilder",
    "WorkspacePatchBuilder",
    "WorkspacePatchEntry",
    "WorkspacePatchManifest",
    "WorkspacePathValidator",
    "WorkspaceTransferEntry",
    "WorkspaceTransferManifest",
    "build_sandbox_backend",
]
