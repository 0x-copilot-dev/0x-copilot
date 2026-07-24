"""Durable, host-write-free workspace overlay domain."""

from agent_runtime.capabilities.workspace.merged_backend import MergedWorkspaceBackend
from agent_runtime.capabilities.workspace.overlay import WorkspaceOverlayService

__all__ = ("MergedWorkspaceBackend", "WorkspaceOverlayService")
