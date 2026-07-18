"""Desktop capability adapters — ai-backend side of the Electron capability broker.

AC5 slice 3a: a read-only Deep Agents backend (``/workspace/``) over user-granted
host folders, driven by a thin async client to the loopback capability broker.
"""

from __future__ import annotations

from agent_runtime.capabilities.desktop.broker_client import (
    BrokerClientConfig,
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
)
from agent_runtime.capabilities.desktop.workspace_backend import (
    ROUTE_PREFIX,
    BrokeredWorkspaceBackend,
    WorkspaceBackendConfig,
    WorkspaceMount,
    WorkspaceMountTable,
    WorkspaceMutationSnapshot,
    WorkspaceSnapshotEmitter,
    WorkspaceSnapshotError,
    WorkspaceSnapshotStore,
    WorkspaceWriteNotSupportedError,
    build_workspace_backend,
)

__all__ = [
    "ROUTE_PREFIX",
    "BrokerClientConfig",
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
    "BrokeredWorkspaceBackend",
    "DesktopBrokerClient",
    "WorkspaceBackendConfig",
    "WorkspaceMount",
    "WorkspaceMountTable",
    "WorkspaceMutationSnapshot",
    "WorkspaceSnapshotEmitter",
    "WorkspaceSnapshotError",
    "WorkspaceSnapshotStore",
    "WorkspaceWriteNotSupportedError",
    "build_workspace_backend",
]
