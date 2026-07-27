"""Public workspace read facade and enforced effect gateway contracts.

Raw overlay mutation primitives are private implementation details of
``workspace.effects`` and are intentionally not re-exported here.
"""

from agent_runtime.capabilities.workspace.merged_backend import MergedWorkspaceBackend
from agent_runtime.capabilities.workspace.patch_plan import (
    MAX_CHANGED_CONTENT_BYTES,
    MAX_CHANGED_FILE_BYTES,
    MAX_HUNKS_PER_OPERATION,
    MAX_PATCH_BYTES,
    MAX_PATCH_HUNKS,
    MAX_PATCH_OPERATIONS,
    MAX_PATCH_PATH_BYTES,
    MAX_PATCH_PATH_DEPTH,
    MAX_PATCH_TARGETS,
    PatchValidationIssue,
    PatchValidationIssueCode,
    PatchValidationReport,
    WorkspaceEditPlanBinding,
    WorkspacePatchExpectedPath,
    WorkspacePatchHunk,
    WorkspacePatchOperation,
    WorkspacePatchOperationKind,
    WorkspacePatchSet,
    WorkspacePatchSetValidator,
    WorkspacePatchTarget,
    WorkspacePatchTargetSet,
)

__all__ = (
    "MAX_CHANGED_CONTENT_BYTES",
    "MAX_CHANGED_FILE_BYTES",
    "MAX_HUNKS_PER_OPERATION",
    "MAX_PATCH_BYTES",
    "MAX_PATCH_HUNKS",
    "MAX_PATCH_OPERATIONS",
    "MAX_PATCH_PATH_BYTES",
    "MAX_PATCH_PATH_DEPTH",
    "MAX_PATCH_TARGETS",
    "MergedWorkspaceBackend",
    "PatchValidationIssue",
    "PatchValidationIssueCode",
    "PatchValidationReport",
    "WorkspaceEditPlanBinding",
    "WorkspacePatchExpectedPath",
    "WorkspacePatchHunk",
    "WorkspacePatchOperation",
    "WorkspacePatchOperationKind",
    "WorkspacePatchSet",
    "WorkspacePatchSetValidator",
    "WorkspacePatchTarget",
    "WorkspacePatchTargetSet",
)
