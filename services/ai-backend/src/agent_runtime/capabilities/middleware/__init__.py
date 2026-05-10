"""Cross-cutting capability middleware (display, budget, auth) used by tool
and MCP registration paths.

Mirrors ``capabilities/skills/`` and ``capabilities/mcp/middleware/`` —
package-scoped helpers that operate at registration / descriptor-build /
agent-call time so the runtime hot path stays free of side effects.
"""

from agent_runtime.capabilities.middleware.display_metadata import (
    DisplayMetadataMiddleware,
    wrap_tool_with_display,
    wrap_tools_with_display,
)

__all__ = [
    "DisplayMetadataMiddleware",
    "wrap_tool_with_display",
    "wrap_tools_with_display",
]
