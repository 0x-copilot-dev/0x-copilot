"""Cross-cutting capability middleware: display metadata, budget guards, and auth helpers."""

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
