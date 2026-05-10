"""Cross-cutting capability middleware (display, budget, auth) used by tool
and MCP registration paths.

Mirrors ``capabilities/skills/`` and ``capabilities/mcp/middleware/`` —
package-scoped helpers that operate at registration / descriptor-build /
agent-call time so the runtime hot path stays free of side effects.
"""

from agent_runtime.capabilities.middleware.display_metadata import (
    DisplayMetadataMiddleware,
)

__all__ = ["DisplayMetadataMiddleware"]
