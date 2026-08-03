"""PR 4.4.6.2 — paused per-chat connectors must be invisible / unloadable / uncallable.

The popover toggle pauses a connector for the conversation. At run-start
the conversation's paused server_ids materialise onto
``AgentRuntimeContext.paused_connectors``. Three gates honor that set:

    1. ``McpPermissionPolicy.is_server_card_authorized`` -> hides the card
       from ``list_server_cards`` and refuses ``load_server``.
    2. the per-tool POLICY stage -> the re-check before a tool call, now
       bound to a fixed ``(card, server, tool)`` rather than one decoded
       from a model payload (see ``mcp/middleware/test_policy_tool.py``).
    3. ``runtime_connector_scopes`` (already correct) -> drops the entry.

These tests pin the gates without booting the full agent graph.
"""

from __future__ import annotations


from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.capabilities.mcp.permissions import McpPermissionPolicy
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig


_LINEAR_SERVER_ID = "seed:linear"
_LINEAR_SERVER_NAME = "linear"


def _model_config() -> ModelConfig:
    return ModelConfig(
        provider="openai",
        model_name="gpt-4o-mini",
        max_input_tokens=4096,
        timeout_seconds=30,
        temperature=0.0,
    )


def _context(*, paused: frozenset[str] = frozenset()) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user_123",
        org_id="org_456",
        roles={"employee"},
        permission_scopes={"search:read", "docs:read"},
        connector_scopes={},
        paused_connectors=paused,
        model_profile=_model_config(),
        trace_id="trace_pause",
    )


def _card(
    *, server_id: str | None = _LINEAR_SERVER_ID, enabled: bool = True
) -> McpServerCard:
    return McpServerCard(
        name=_LINEAR_SERVER_NAME,
        server_id=server_id,
        short_description="Linear issues, projects, and cycles.",
        transport=McpTransport.HTTP,
        auth_mode=McpAuthMode.OAUTH2,
        required_scopes=frozenset({"docs:read"}),
        health=McpServerHealth.HEALTHY,
        load_cost=10,
        enabled=enabled,
    )


class TestMcpPermissionPolicyPaused:
    def test_authorized_when_paused_set_does_not_include_server(self) -> None:
        ctx = _context(paused=frozenset({"seed:other"}))
        assert McpPermissionPolicy.is_server_card_authorized(ctx, _card()) is True

    def test_authorized_when_no_pauses(self) -> None:
        ctx = _context()
        assert McpPermissionPolicy.is_server_card_authorized(ctx, _card()) is True

    def test_unauthorized_when_server_id_paused(self) -> None:
        ctx = _context(paused=frozenset({_LINEAR_SERVER_ID}))
        assert McpPermissionPolicy.is_server_card_authorized(ctx, _card()) is False

    def test_visible_check_also_blocked_when_paused(self) -> None:
        ctx = _context(paused=frozenset({_LINEAR_SERVER_ID}))
        assert McpPermissionPolicy.is_server_card_visible(ctx, _card()) is False

    def test_card_without_server_id_skips_paused_gate(self) -> None:
        # Deployment-level cards (not user-installed) have ``server_id=None``
        # and must still authorize against RBAC; the paused gate doesn't
        # reach them because there's no key to match against.
        ctx = _context(paused=frozenset({_LINEAR_SERVER_ID}))
        assert (
            McpPermissionPolicy.is_server_card_authorized(ctx, _card(server_id=None))
            is True
        )


class _StubProvider:
    def __init__(self, card: McpServerCard) -> None:
        self.card = card

    def create_client(self, _card: McpServerCard) -> object:
        # If the gate fails open, the call would reach here and raise —
        # the gate must short-circuit before client creation.
        raise AssertionError("create_client must not be called for a paused server")


class _StubResolution:
    def __init__(self, card: McpServerCard) -> None:
        self.card = card
        self.provider = _StubProvider(card)


class _StubRegistry:
    def __init__(self, card: McpServerCard) -> None:
        self._card = card

    async def resolve_server(self, _name: str) -> _StubResolution:
        return _StubResolution(self._card)


class _StubLoader:
    timeout_seconds = 5.0
