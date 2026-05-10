"""End-to-end test that ``BackendMcpClient._tool_descriptor`` populates
``McpToolDescriptor.display`` via ``DisplayMetadataMiddleware`` (polish-removal
Phase 2.A).

See ``docs/refactor/01-presentation-polish-removal.md`` §4 Phase 2.A.
"""

from __future__ import annotations

from agent_runtime.capabilities.mcp.backend_provider import BackendMcpClient
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.execution.contracts import AgentRuntimeContext


def _client_with_card(
    runtime_context: AgentRuntimeContext,
    *,
    display_name: str | None = "Linear",
    name: str = "linear",
) -> BackendMcpClient:
    card = McpServerCard(
        name=name,
        display_name=display_name,
        short_description="Linear ticket management",
        transport=McpTransport.HTTP,
        auth_mode=McpAuthMode.OAUTH2,
        required_scopes=("linear:read",),
        health=McpServerHealth.HEALTHY,
        load_cost=1,
    )
    return BackendMcpClient(
        backend_url="http://backend.local",
        runtime_context=runtime_context,
        card=card,
    )


def test_tool_descriptor_populates_display_with_synthesised_template(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    client = _client_with_card(runtime_context_admin)

    descriptor = client._tool_descriptor(
        {
            "name": "list_issues",
            "description": "List Linear issues matching a filter.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "status": {"type": "string"},
                },
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "title": {"type": "string"},
                                "url": {"type": "string"},
                                "status": {"type": "string"},
                            },
                        },
                    }
                },
            },
        }
    )

    assert descriptor.name == "list_issues"
    assert descriptor.display is not None, (
        "MCP descriptors must carry a display template"
    )
    assert descriptor.display.synthetic is True
    assert descriptor.display.title_template == "List Linear issues for {query}"
    assert descriptor.display.result_title_template == "Linear results"
    assert descriptor.display.result_preview_path == "items"
    assert descriptor.display.result_preview_row == {
        "title": "title",
        "subtitle": "status",
        "url": "url",
    }


def test_tool_descriptor_falls_back_to_card_name_when_display_name_absent(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    """Some MCP servers ship without a ``display_name``. The synthesiser
    should still produce a sensible (if slightly awkward) title using the
    bare ``card.name``."""

    client = _client_with_card(
        runtime_context_admin,
        display_name=None,
        name="acme_pipeline",
    )

    descriptor = client._tool_descriptor(
        {
            "name": "list_runs",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        }
    )

    assert descriptor.display is not None
    # ``acme_pipeline`` → ``Acme Pipeline`` (humanised). No ``_io``/``_app``
    # suffix to strip; snake_case → Title Case.
    assert descriptor.display.title_template == "List Acme Pipeline runs for {query}"


def test_tool_descriptor_handles_missing_output_schema(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    """Vendors often omit ``outputSchema``. The descriptor still loads,
    and the synthesiser produces ``result_preview_path=None`` so the
    payload projector falls back to its own heuristics at render time."""

    client = _client_with_card(runtime_context_admin)

    descriptor = client._tool_descriptor(
        {
            "name": "search_issues",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        }
    )

    assert descriptor.display is not None
    assert descriptor.display.title_template == "Search Linear issues for {query}"
    assert descriptor.display.result_preview_path is None
    assert descriptor.display.result_preview_row is None


def test_tool_descriptor_registers_into_active_mcp_display_context(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    """Phase 2.B — when a per-run ``McpDisplayRegistryContext`` is bound,
    every descriptor built via ``_tool_descriptor`` lands in the registry
    keyed by tool name. This is what makes the synthesised template
    visible to ``PresentationGenerator`` for ``call_mcp_tool`` events.
    """

    from agent_runtime.capabilities.mcp.descriptor_registry import (
        McpDisplayRegistryContext,
    )
    from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate

    client = _client_with_card(runtime_context_admin)
    registry: dict[str, ToolDisplayTemplate] = {}
    token = McpDisplayRegistryContext.bind_for_run(registry)
    try:
        client._tool_descriptor(
            {
                "name": "list_issues",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            }
        )
        client._tool_descriptor(
            {
                "name": "create_issue",
                "inputSchema": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                },
            }
        )
    finally:
        McpDisplayRegistryContext.unbind(token)

    assert set(registry.keys()) == {"list_issues", "create_issue"}
    assert registry["list_issues"].synthetic is True
    assert registry["list_issues"].title_template == "List Linear issues for {query}"
    assert registry["create_issue"].title_template == "Create Linear issue for {title}"


def test_tool_descriptor_register_is_safe_when_no_context_bound(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    """Replay / eval / unit-test paths build descriptors outside a run
    context. Registration must be a no-op there — never raise, never
    leak state. Pins the safety contract."""

    from agent_runtime.capabilities.mcp.descriptor_registry import (
        McpDisplayRegistryContext,
    )

    assert McpDisplayRegistryContext.active() is None

    client = _client_with_card(runtime_context_admin)
    descriptor = client._tool_descriptor(
        {
            "name": "list_issues",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        }
    )

    # Descriptor still built normally — just nothing was registered anywhere.
    assert descriptor.display is not None
    assert McpDisplayRegistryContext.active() is None


def test_tool_descriptor_synthesises_for_unknown_verb_with_synthetic_flag(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    """Custom tool names that don't match a verb prefix still get a
    synthesised template (``synthetic=True``) — the agent's ``_display_*``
    in Phase 3 is welcome to override these."""

    client = _client_with_card(runtime_context_admin)

    descriptor = client._tool_descriptor(
        {
            "name": "run_workflow",
            "inputSchema": {
                "type": "object",
                "properties": {"workflow_id": {"type": "string"}},
            },
        }
    )

    assert descriptor.display is not None
    assert descriptor.display.synthetic is True
    assert descriptor.display.title_template == "Linear: Run Workflow"
