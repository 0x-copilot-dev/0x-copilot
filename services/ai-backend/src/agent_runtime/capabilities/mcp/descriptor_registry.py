"""Per-run mutable registry of MCP tool name → display template.

Polish-removal Phase 2.B (docs/refactor/01-presentation-polish-removal.md).

MCP descriptors load **lazily** during a run: when the agent calls the
``load_mcp_server`` builtin, ``BackendMcpClient.list_tools()`` returns
``McpToolDescriptor`` instances whose ``display`` field was synthesised by
``DisplayMetadataMiddleware`` (Phase 2.A). To make those templates visible
to ``PresentationGenerator._resolve_tool_template`` without threading a
reference through every event-emit call, we keep a per-run mutable map
keyed by MCP tool name and bind it via :class:`ContextVar` at the run
handler entry — same shape as
:class:`agent_runtime.capabilities.citations.CitationLedger`'s binding.

The registry is intentionally **dumb**:

- One entry per ``(tool_name → ToolDisplayTemplate)``.
- ``register`` is a no-op when nothing is bound (replay / eval / unit tests
  that don't need the lookup): the lazy-load callsite never has to know
  whether a run is active.
- ``get`` returns ``None`` when nothing is bound or no entry matches —
  the presentation generator's existing fallthrough handles that case.

A run handler typically does:

    registry: dict[str, ToolDisplayTemplate] = {}
    token = McpDisplayRegistryContext.bind_for_run(registry)
    try:
        ...  # run body — descriptors land in ``registry`` as servers load
    finally:
        McpDisplayRegistryContext.unbind(token)
"""

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
        """Record a synthesised template for ``tool_name`` on the active run.

        No-op when no registry is bound (replay / eval / unit-test paths).
        Last write wins on duplicate names; this matches the runtime's
        existing duplicate-name handling — the ``DynamicMcpRegistry``
        rejects duplicate *server* names at registration time, so the
        only realistic source of duplicates is two distinct servers
        exposing tools that happen to share a name.
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
