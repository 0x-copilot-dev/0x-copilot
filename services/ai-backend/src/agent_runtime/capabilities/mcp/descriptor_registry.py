"""Per-run ContextVar registry mapping MCP tool names to their synthesised display templates."""

from __future__ import annotations

from contextvars import ContextVar

from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate

_MCP_DISPLAY_REGISTRY_CTX: ContextVar[dict[str, ToolDisplayTemplate] | None] = (
    ContextVar("mcp_display_registry", default=None)
)


class McpDisplayRegistryContext:
    """Per-run binding for the MCP-tool-name → display-template registry."""

    @classmethod
    def bind_for_run(cls, registry: dict[str, ToolDisplayTemplate]) -> object:
        """Set the active registry; return the previous token for restoration."""

        return _MCP_DISPLAY_REGISTRY_CTX.set(registry)

    @classmethod
    def unbind(cls, token: object) -> None:
        """Restore the previous binding. Safe to call with the bind result."""

        _MCP_DISPLAY_REGISTRY_CTX.reset(token)  # type: ignore[arg-type]

    @classmethod
    def active(cls) -> dict[str, ToolDisplayTemplate] | None:
        """Return the active registry or ``None`` (test helper / debugging)."""

        return _MCP_DISPLAY_REGISTRY_CTX.get(None)

    @classmethod
    def register(cls, tool_name: str, template: ToolDisplayTemplate) -> None:
        """Record a synthesised display template for ``tool_name`` on the active run.

        No-op when no registry is bound (replay / eval / unit tests). Last write
        wins on duplicate tool names — two servers may expose identically named tools.
        """

        registry = _MCP_DISPLAY_REGISTRY_CTX.get(None)
        if registry is None:
            return
        registry[tool_name] = template

    @classmethod
    def get(cls, tool_name: str) -> ToolDisplayTemplate | None:
        """Return the template for ``tool_name`` if any; else ``None``."""

        registry = _MCP_DISPLAY_REGISTRY_CTX.get(None)
        if registry is None:
            return None
        return registry.get(tool_name)
