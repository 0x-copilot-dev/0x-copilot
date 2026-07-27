"""Durable, host-write-free workspace overlay domain."""

from agent_runtime.capabilities.workspace.merged_backend import MergedWorkspaceBackend
from agent_runtime.capabilities.workspace.overlay import WorkspaceOverlayService
from agent_runtime.capabilities.workspace.patch_plan import (
    PatchValidationIssue,
    PatchValidationIssueCode,
    PatchValidationReport,
    WorkspacePatchHunk,
    WorkspacePatchOperation,
    WorkspacePatchOperationKind,
    WorkspacePatchSet,
    WorkspacePatchSetValidator,
    WorkspacePatchTarget,
    WorkspacePatchTargetSet,
)

__all__ = (
    "MergedWorkspaceBackend",
    "PatchValidationIssue",
    "PatchValidationIssueCode",
    "PatchValidationReport",
    "WorkspaceOverlayService",
    "WorkspacePatchHunk",
    "WorkspacePatchOperation",
    "WorkspacePatchOperationKind",
    "WorkspacePatchSet",
    "WorkspacePatchSetValidator",
    "WorkspacePatchTarget",
    "WorkspacePatchTargetSet",
)
