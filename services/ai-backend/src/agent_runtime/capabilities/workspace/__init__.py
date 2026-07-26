"""Public workspace read facade and enforced effect gateway contracts.

Raw overlay mutation primitives are private implementation details of
``workspace.effects`` and are intentionally not re-exported here.
"""

from agent_runtime.capabilities.workspace.merged_backend import MergedWorkspaceBackend

__all__ = ("MergedWorkspaceBackend",)
