"""Desktop capability adapters — ai-backend side of the Electron capability broker.

AC5 slice 3a: a read-only Deep Agents backend (``/workspace/``) over user-granted
host folders, driven by a thin async client to the loopback capability broker.

A host-absolute path the agent supplies is classified here
(:mod:`~agent_runtime.capabilities.desktop.host_path`) and, when no grant covers
it, becomes a blocking grant request
(:mod:`~agent_runtime.capabilities.desktop.workspace_grant`) rather than an empty
listing. ``HostPathClassifier.is_host_shaped`` /
``BrokeredWorkspaceBackend.claims_path`` are what a router consults so such a
path can never fall through to a virtual backend, and
:func:`~agent_runtime.capabilities.desktop.host_route.guarded_default` is the seam
that applies the claim where it has to be applied — the composite's DEFAULT, since
a host path is not a prefix and can never be a route.
"""

from __future__ import annotations

from agent_runtime.capabilities.desktop.broker_client import (
    BrokerClientConfig,
    BrokerCapabilityRetiredError,
    BrokerError,
    BrokerGrant,
    BrokerGrantRequiredError,
    BrokerGrantSnapshot,
    BrokerInvalidPathError,
    BrokerInvalidRequestError,
    BrokerNotADirectoryError,
    BrokerNotAFileError,
    BrokerNotFoundError,
    BrokerPermissionDeniedError,
    BrokerProtocolError,
    BrokerTooLargeError,
    BrokerUnavailableError,
    BrokerUnsupportedError,
    DesktopBrokerClient,
    McpSecretResult,
    WorkspaceAuthorityDeniedError,
    WorkspaceCommitResult,
    WorkspaceConflictError,
    WorkspaceHostSession,
    WorkspaceHostSessionGrant,
    WorkspacePreconditionDriftError,
    WorkspacePreparedEffect,
    WorkspaceUploadSlot,
)
from agent_runtime.capabilities.desktop.host_floor import (
    HostFilesystemFloor,
    HostFloorMessages,
)
from agent_runtime.capabilities.desktop.host_path import (
    ClassifiedPath,
    HostPathClassifier,
    HostPathFlavour,
    HostPathKind,
    HostPathMessages,
    HostPathRefusal,
    HostRootIndex,
    HostRootMatch,
)
from agent_runtime.capabilities.desktop.host_route import (
    HostPathGuardBackend,
    guarded_default,
)
from agent_runtime.capabilities.desktop.host_tool_paths import (
    HostFsToolArgs,
    HostPathToolMiddleware,
    NativeHostPathBackend,
)
from agent_runtime.capabilities.desktop.workspace_authority import (
    BrokerWorkspaceAuthority,
    ImmutableWorkspaceContentResolver,
    WorkspaceAuthorityContractError,
    WorkspaceAuthorityPort,
    WorkspaceChangeEntry,
    WorkspaceEffectExecutor,
    WorkspacePrecondition,
    WorkspacePrepareCommand,
    WorkspaceProposalMaterial,
    WorkspaceProposalResolver,
)
from agent_runtime.capabilities.desktop.workspace_backend import (
    ROUTE_PREFIX,
    BrokeredWorkspaceBackend,
    WorkspaceBackendConfig,
    WorkspaceMount,
    WorkspaceMountTable,
    WorkspaceWriteNotSupportedError,
    build_workspace_backend,
)
from agent_runtime.capabilities.desktop.workspace_grant import (
    WorkspaceGrantGate,
    WorkspaceGrantMessages,
    WorkspaceGrantOutcome,
    WorkspaceGrantRequest,
    WorkspaceGrantResume,
    WorkspaceGrantValues,
)

__all__ = [
    "ROUTE_PREFIX",
    "BrokerClientConfig",
    "BrokerCapabilityRetiredError",
    "BrokerError",
    "BrokerGrant",
    "BrokerGrantRequiredError",
    "BrokerGrantSnapshot",
    "BrokerInvalidPathError",
    "BrokerInvalidRequestError",
    "BrokerNotADirectoryError",
    "BrokerNotAFileError",
    "BrokerNotFoundError",
    "BrokerPermissionDeniedError",
    "BrokerProtocolError",
    "BrokerTooLargeError",
    "BrokerUnavailableError",
    "BrokerUnsupportedError",
    "BrokerWorkspaceAuthority",
    "BrokeredWorkspaceBackend",
    "ClassifiedPath",
    "DesktopBrokerClient",
    "HostFilesystemFloor",
    "HostFloorMessages",
    "HostFsToolArgs",
    "HostPathClassifier",
    "HostPathFlavour",
    "HostPathGuardBackend",
    "HostPathKind",
    "HostPathMessages",
    "HostPathRefusal",
    "HostPathToolMiddleware",
    "HostRootIndex",
    "HostRootMatch",
    "ImmutableWorkspaceContentResolver",
    "McpSecretResult",
    "NativeHostPathBackend",
    "WorkspaceBackendConfig",
    "WorkspaceAuthorityContractError",
    "WorkspaceAuthorityDeniedError",
    "WorkspaceAuthorityPort",
    "WorkspaceChangeEntry",
    "WorkspaceCommitResult",
    "WorkspaceConflictError",
    "WorkspaceGrantGate",
    "WorkspaceGrantMessages",
    "WorkspaceGrantOutcome",
    "WorkspaceGrantRequest",
    "WorkspaceGrantResume",
    "WorkspaceGrantValues",
    "WorkspaceHostSession",
    "WorkspaceHostSessionGrant",
    "WorkspaceEffectExecutor",
    "WorkspaceMount",
    "WorkspaceMountTable",
    "WorkspacePrecondition",
    "WorkspacePreconditionDriftError",
    "WorkspacePrepareCommand",
    "WorkspacePreparedEffect",
    "WorkspaceProposalMaterial",
    "WorkspaceProposalResolver",
    "WorkspaceWriteNotSupportedError",
    "WorkspaceUploadSlot",
    "build_workspace_backend",
    "guarded_default",
]
