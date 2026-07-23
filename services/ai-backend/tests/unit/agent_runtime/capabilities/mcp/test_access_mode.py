"""PRD-06 D3b — connector access-mode enforcement in the runtime.

(a) ``McpPermissionPolicy.is_server_card_authorized`` denies a server whose
    ``context.connector_access_modes[server_id]`` is ``off`` (the frozen
    defense-in-depth mirror of the authoritative backend gate).
(b) ``BackendMcpClient._tool_descriptor`` parses MCP
    ``annotations.readOnlyHint`` into ``McpToolDescriptor.read_only``:
    ``True`` for ``{"annotations":{"readOnlyHint":true}}``; ``None`` when no
    ``annotations`` block is present (fail-closed).
"""

from __future__ import annotations

from agent_runtime.capabilities.mcp.backend_provider import BackendMcpClient
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.capabilities.mcp.permissions import McpPermissionPolicy
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig

_SERVER_ID = "seed:gmail"
_SERVER_NAME = "gmail"


def _model_config() -> ModelConfig:
    return ModelConfig(
        provider="openai",
        model_name="gpt-4o-mini",
        max_input_tokens=4096,
        timeout_seconds=30,
        temperature=0.0,
    )


def _context(*, access_modes: dict[str, str] | None = None) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user_123",
        org_id="org_456",
        roles={"employee"},
        permission_scopes={"docs:read"},
        connector_access_modes=access_modes or {},
        model_profile=_model_config(),
        trace_id="trace_access_mode",
    )


def _card() -> McpServerCard:
    return McpServerCard(
        name=_SERVER_NAME,
        server_id=_SERVER_ID,
        short_description="Gmail threads and labels.",
        transport=McpTransport.HTTP,
        auth_mode=McpAuthMode.OAUTH2,
        required_scopes=frozenset({"docs:read"}),
        health=McpServerHealth.HEALTHY,
        load_cost=10,
    )


class TestAccessModeAuthorization:
    def test_off_mode_denies_authorization(self) -> None:
        ctx = _context(access_modes={_SERVER_ID: "off"})
        assert McpPermissionPolicy.is_server_card_authorized(ctx, _card()) is False

    def test_read_mode_authorizes(self) -> None:
        ctx = _context(access_modes={_SERVER_ID: "read"})
        assert McpPermissionPolicy.is_server_card_authorized(ctx, _card()) is True

    def test_read_act_mode_authorizes(self) -> None:
        ctx = _context(access_modes={_SERVER_ID: "read_act"})
        assert McpPermissionPolicy.is_server_card_authorized(ctx, _card()) is True

    def test_absent_mode_authorizes(self) -> None:
        ctx = _context(access_modes={})
        assert McpPermissionPolicy.is_server_card_authorized(ctx, _card()) is True


def _client() -> BackendMcpClient:
    return BackendMcpClient(
        backend_url="http://backend.local",
        runtime_context=_context(),
        card=_card(),
    )


class TestToolDescriptorReadOnly:
    _BASE = {
        "name": "list_threads",
        "description": "List Gmail threads.",
        "inputSchema": {"type": "object", "properties": {}},
    }

    def test_read_only_hint_true_sets_read_only_true(self) -> None:
        descriptor = _client()._tool_descriptor(
            {**self._BASE, "annotations": {"readOnlyHint": True}}
        )
        assert descriptor.read_only is True

    def test_absent_annotations_leaves_read_only_none(self) -> None:
        descriptor = _client()._tool_descriptor({**self._BASE})
        assert descriptor.read_only is None

    def test_read_only_hint_false_sets_read_only_false(self) -> None:
        descriptor = _client()._tool_descriptor(
            {**self._BASE, "annotations": {"readOnlyHint": False}}
        )
        assert descriptor.read_only is False
