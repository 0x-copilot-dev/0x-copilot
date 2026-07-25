"""Safe domain failures for the virtual workspace overlay."""

from __future__ import annotations


class WorkspaceOverlayError(RuntimeError):
    """Base error whose message is safe to expose to an agent."""


class WorkspaceOverlayConflictError(WorkspaceOverlayError):
    """The caller attempted to append against a stale manifest version."""

    def __init__(self) -> None:
        super().__init__("Workspace overlay changed; retry the mutation.")


class WorkspacePathError(WorkspaceOverlayError):
    """A path is outside the permitted virtual workspace namespace."""


class WorkspaceNotFoundError(WorkspaceOverlayError):
    """A file or directory is absent from the merged virtual workspace."""

    def __init__(self) -> None:
        super().__init__("Workspace path was not found.")


class WorkspaceIsDirectoryError(WorkspaceOverlayError):
    """A file operation targeted a directory."""

    def __init__(self) -> None:
        super().__init__("Workspace path is a directory.")


class WorkspaceEditError(WorkspaceOverlayError):
    """A strict text edit cannot be applied to the merged file contents."""


class WorkspaceLimitError(WorkspaceOverlayError):
    """A bounded overlay limit was exceeded before a mutation was appended."""
