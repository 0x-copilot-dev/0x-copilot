"""Desktop-local agentic browser capability (AC8).

Reads use the MCP-provider seam; exact staged effects use the closed A5 bridge.
Both fail closed unless the trusted desktop supervisor injects its loopback
broker URL + credential.
"""

from __future__ import annotations

from agent_runtime.capabilities.browser.desktop_browser_provider import (
    BrowserMcpConfig,
    DesktopBrowserMcpClient,
    DesktopBrowserMcpProvider,
    build_browser_mcp,
)
from agent_runtime.capabilities.browser.desktop_effect_bridge import (
    DesktopBrowserEffectBridge,
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
    "DesktopBrowserEffectBridge",
    "build_browser_mcp",
]
