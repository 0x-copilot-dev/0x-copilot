"""Compatibility aliases for the pre-D1 MCP executor import path.

New worker composition imports :mod:`runtime_worker.mcp_effect_executor`.
Keeping this shim preserves already-landed A5 imports without leaving a second
MCP mutation implementation in the tree.
"""

from runtime_worker.mcp_effect_executor import (
    McpEffectExecutor as LegacyMcpEffectExecutor,
    McpEffectExecutorDisabledError as LegacyMcpEffectExecutorDisabledError,
    McpEffectMaterial as LegacyMcpEffectMaterial,
    McpEffectMaterialError as LegacyMcpEffectMaterialError,
    McpEffectMaterialResolver as LegacyMcpEffectMaterialResolver,
)

__all__ = [
    "LegacyMcpEffectExecutor",
    "LegacyMcpEffectExecutorDisabledError",
    "LegacyMcpEffectMaterial",
    "LegacyMcpEffectMaterialError",
    "LegacyMcpEffectMaterialResolver",
]
