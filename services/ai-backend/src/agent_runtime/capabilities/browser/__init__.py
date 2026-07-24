"""Desktop-local agentic browser MCP capability (AC8, read-only foundation).

Public surface: the ``build_browser_mcp`` seam (consumed by the runtime factory
without editing it) plus the provider/client/config types. The card and tools
appear ONLY under the single-user desktop profile with the feature enabled and a
broker configured; otherwise the seam returns ``None`` and nothing is exposed.
"""

from __future__ import annotations

from agent_runtime.capabilities.browser.desktop_browser_provider import (
    BrowserMcpConfig,
    DesktopBrowserMcpClient,
    DesktopBrowserMcpProvider,
    build_browser_mcp,
)
from agent_runtime.capabilities.browser.effect_adapter import (
    BrowserEffectExecutor,
    BrowserEffectStageAdapter,
)
from agent_runtime.capabilities.browser.operation_adapter import BrowserOperationAdapter

__all__ = [
    "BrowserMcpConfig",
    "BrowserEffectExecutor",
    "BrowserEffectStageAdapter",
    "BrowserOperationAdapter",
    "DesktopBrowserMcpClient",
    "DesktopBrowserMcpProvider",
    "build_browser_mcp",
]
