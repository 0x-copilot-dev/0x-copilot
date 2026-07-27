from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_runtime.capabilities.discovery import (
    ApprovalCue,
    AuthorizedCatalogBuilder,
    CapabilityCatalogScope,
    CapabilitySource,
)
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.capabilities.tools.cards import ToolCard, ToolRiskLevel
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig

_NOW = datetime(2026, 7, 27, 12, tzinfo=UTC)
_REFERENCE_KEY = b"catalog-test-reference-key-32-bytes!!"


def _context(
    *,
    run_id: str = "run_1",
    user_id: str = "user_1",
    paused_connectors: frozenset[str] = frozenset(),
) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id=user_id,
        org_id="org_1",
        roles={"member"},
        permission_scopes={"docs:read", "calendar:read"},
        connector_scopes={
            "drive": frozenset({"docs:read"}),
            "calendar": frozenset({"calendar:read"}),
        },
        paused_connectors=paused_connectors,
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-test",
            max_input_tokens=32_000,
            timeout_seconds=30,
            temperature=0,
        ),
        run_id=run_id,
    )


def _scope(context: AgentRuntimeContext) -> CapabilityCatalogScope:
    return CapabilityCatalogScope.from_context(
        context,
        profile_id="research",
        policy_revision="policy_7",
        connector_scope_revision="scope_9",
    )


def _tool(
    *,
    name: str,
    connector: str = "drive",
    scopes: frozenset[str] = frozenset({"docs:read"}),
    enabled: bool = True,
    risk: ToolRiskLevel = ToolRiskLevel.LOW,
) -> ToolCard:
    return ToolCard(
        name=name,
        display_name=name.replace("_", " ").title(),
        short_description=f"Use {name} to find relevant records.",
        connector=connector,
        tags={"search", "records"},
        required_scopes=scopes,
        risk_level=risk,
        load_cost=1,
        enabled=enabled,
    )


def _server(
    *,
    name: str,
    server_id: str | None = None,
    scopes: frozenset[str] = frozenset({"docs:read"}),
    health: McpServerHealth = McpServerHealth.HEALTHY,
) -> McpServerCard:
    return McpServerCard(
        name=name,
        server_id=server_id,
        display_name=name.replace("_", " ").title(),
        short_description=f"{name} compact server metadata.",
        transport=McpTransport.HTTP,
        auth_mode=McpAuthMode.OAUTH2,
        required_scopes=scopes,
        health=health,
        load_cost=2,
        connector_slug=name,
    )


def _build(
    *,
    context: AgentRuntimeContext,
    tools: tuple[ToolCard, ...] = (),
    servers: tuple[McpServerCard, ...] = (),
):
    return AuthorizedCatalogBuilder(reference_key=_REFERENCE_KEY).build(
        context=context,
        scope=_scope(context),
        tool_cards=tools,
        mcp_server_cards=servers,
        deferred_schema_tokens=2_000,
        expires_at=_NOW + timedelta(minutes=15),
    )


class TestAuthorizedCatalogBuilder:
    def test_projects_only_authorized_compact_cards(self) -> None:
        context = _context(paused_connectors=frozenset({"seed:paused"}))

        catalog = _build(
            context=context,
            tools=(
                _tool(name="drive_search"),
                _tool(
                    name="admin_search",
                    scopes=frozenset({"admin:read"}),
                ),
                _tool(name="disabled_search", enabled=False),
            ),
            servers=(
                _server(name="drive_server", server_id="seed:drive"),
                _server(name="paused_server", server_id="seed:paused"),
                _server(
                    name="unavailable_server",
                    health=McpServerHealth.UNAVAILABLE,
                ),
            ),
        )

        assert [(entry.source, entry.stable_name) for entry in catalog.entries] == [
            (CapabilitySource.MCP_SERVER, "drive_server"),
            (CapabilitySource.TOOL_CARD, "drive_search"),
        ]
        assert catalog.revision.descriptor_count == 2
        assert catalog.revision.deferred_schema_tokens == 2_000

    def test_catalog_is_stable_when_source_order_changes(self) -> None:
        context = _context()
        tools = (_tool(name="beta_search"), _tool(name="alpha_search"))
        servers = (
            _server(name="zeta_server"),
            _server(name="calendar_server", scopes=frozenset({"calendar:read"})),
        )

        first = _build(context=context, tools=tools, servers=servers)
        second = _build(
            context=context,
            tools=tuple(reversed(tools)),
            servers=tuple(reversed(servers)),
        )

        assert first == second
        assert first.revision.revision == second.revision.revision

    def test_refs_are_opaque_and_change_with_run_scope(self) -> None:
        first_context = _context(run_id="run_1")
        second_context = _context(run_id="run_2")
        card = _tool(name="private_tool_name")

        first = _build(context=first_context, tools=(card,))
        second = _build(context=second_context, tools=(card,))

        assert first.revision.catalog_id != second.revision.catalog_id
        assert first.entries[0].capability_ref != second.entries[0].capability_ref
        assert "private_tool_name" not in first.entries[0].capability_ref

    def test_high_risk_card_discloses_policy_dependent_approval(self) -> None:
        catalog = _build(
            context=_context(),
            tools=(_tool(name="delete_record", risk=ToolRiskLevel.HIGH),),
        )

        assert catalog.entries[0].approval_cue is ApprovalCue.POLICY_DEPENDENT

    def test_scope_mismatch_fails_closed(self) -> None:
        context = _context(user_id="user_1")
        other_context = _context(user_id="user_2")

        with pytest.raises(ValueError, match="does not match"):
            AuthorizedCatalogBuilder(reference_key=_REFERENCE_KEY).build(
                context=other_context,
                scope=_scope(context),
                expires_at=_NOW + timedelta(minutes=15),
            )

    def test_reference_key_must_be_strong(self) -> None:
        with pytest.raises(ValueError, match="at least 32 bytes"):
            AuthorizedCatalogBuilder(reference_key=b"short")

    def test_active_check_binds_subject_and_expiry(self) -> None:
        context = _context()
        catalog = _build(context=context, tools=(_tool(name="drive_search"),))

        assert catalog.is_active_for(context, now=_NOW)
        assert not catalog.is_active_for(
            context,
            now=_NOW + timedelta(minutes=16),
        )
        assert not catalog.is_active_for(
            _context(user_id="user_2"),
            now=_NOW,
        )
