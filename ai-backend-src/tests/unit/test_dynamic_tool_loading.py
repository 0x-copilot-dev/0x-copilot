from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError

from enterprise_search_ai.agent.contracts import AgentRuntimeContext, ModelConfig, RuntimeErrorCode
from enterprise_search_ai.agent.errors import AgentRuntimeError
from enterprise_search_ai.tools import (
    DynamicToolRegistry,
    LoadedToolSpec,
    ToolCard,
    ToolLoadErrorCode,
    ToolLoadRequest,
    ToolLoader,
    ToolPermissionPolicy,
    ToolRiskLevel,
    ToolSideEffect,
)


@dataclass
class FakeSpecProvider:
    cards: Sequence[ToolCard | Mapping[str, object]]
    specs: Mapping[str, LoadedToolSpec | Mapping[str, object]]
    fail_on_load: bool = False
    loaded_names: list[str] = field(default_factory=list)

    def list_tool_cards(self) -> Sequence[ToolCard | Mapping[str, object]]:
        return self.cards

    def load_tool_spec(self, name: str) -> LoadedToolSpec | Mapping[str, object]:
        self.loaded_names.append(name)
        if self.fail_on_load:
            raise RuntimeError("connector token=super-secret")
        return self.specs[name]


def test_tool_card_normalizes_compact_metadata() -> None:
    card = make_card(
        name="Doc_Search",
        connector="Google-Drive",
        tags={"Docs", "Search"},
        required_scopes={"Docs:Read"},
    )

    assert card.name == "doc_search"
    assert card.connector == "google-drive"
    assert card.tags == frozenset({"docs", "search"})
    assert card.required_scopes == frozenset({"docs:read"})


def test_tool_card_rejects_bad_slug_and_oversized_description() -> None:
    with pytest.raises(ValidationError):
        make_card(name="Doc Search")

    with pytest.raises(ValidationError):
        make_card(short_description="x" * 241)


def test_loaded_tool_spec_validates_schema_and_risk_policy() -> None:
    spec = make_spec(
        name="doc_search",
        risk_level=ToolRiskLevel.MEDIUM,
        side_effects={ToolSideEffect.READ, ToolSideEffect.EXTERNAL_CALL},
    )

    assert spec.args_schema["type"] == "object"
    assert spec.permission_policy.risk_level == ToolRiskLevel.MEDIUM

    with pytest.raises(ValidationError):
        make_spec(name="doc_search", args_schema={"properties": {}})

    with pytest.raises(ValidationError):
        ToolPermissionPolicy(
            connector="google-drive",
            required_scopes={"docs:read"},
            risk_level=ToolRiskLevel.HIGH,
            requires_confirmation=False,
        )


def test_registry_returns_only_authorized_cards(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    provider = FakeSpecProvider(
        cards=(
            make_card(name="doc_search"),
            make_card(name="slack_search", connector="slack", required_scopes={"chat:read"}),
            make_card(name="disabled_tool", enabled=False),
        ),
        specs={},
    )
    registry = DynamicToolRegistry(providers=(provider,))

    cards = registry.list_tool_cards(runtime_context_admin)

    assert tuple(card.name for card in cards) == ("doc_search",)


def test_registry_duplicate_names_raise_deterministic_error(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    provider = FakeSpecProvider(
        cards=(
            make_card(name="doc_search", connector="google-drive"),
            make_card(name="doc_search", connector="slack"),
        ),
        specs={},
    )
    registry = DynamicToolRegistry(providers=(provider,))

    with pytest.raises(AgentRuntimeError) as exc_info:
        registry.list_tool_cards(runtime_context_admin)

    assert exc_info.value.code == RuntimeErrorCode.CONFIGURATION_ERROR
    assert exc_info.value.safe_message == "Multiple tools are registered with the same name."


def test_loader_returns_validated_spec_after_permission_recheck(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    spec = make_spec(name="doc_search")
    provider = FakeSpecProvider(cards=(make_card(name="doc_search"),), specs={"doc_search": spec})
    loader = ToolLoader(DynamicToolRegistry(providers=(provider,)))

    result = loader.load_tool(
        ToolLoadRequest(tool_name="doc_search", runtime_context=runtime_context_admin)
    )

    assert result.succeeded
    assert result.loaded_spec == spec
    assert provider.loaded_names == ["doc_search"]


def test_loader_rejects_unknown_duplicate_and_display_name_requests(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    provider = FakeSpecProvider(
        cards=(
            make_card(name="doc_search", connector="google-drive"),
            make_card(name="doc_search", connector="slack"),
        ),
        specs={},
    )
    loader = ToolLoader(DynamicToolRegistry(providers=(provider,)))

    duplicate_result = loader.load_tool(
        ToolLoadRequest(tool_name="doc_search", runtime_context=runtime_context_admin)
    )
    unknown_result = loader.load_tool(
        ToolLoadRequest(tool_name="missing_tool", runtime_context=runtime_context_admin)
    )
    display_name_result = loader.load_tool_by_name(
        tool_name="Doc Search",
        runtime_context=runtime_context_admin,
    )

    assert duplicate_result.error is not None
    assert duplicate_result.error.code == ToolLoadErrorCode.DUPLICATE_TOOL_NAME
    assert duplicate_result.error.correlation_id == runtime_context_admin.trace_id
    assert unknown_result.error is not None
    assert unknown_result.error.code == ToolLoadErrorCode.UNKNOWN_TOOL
    assert display_name_result.error is not None
    assert display_name_result.error.code == ToolLoadErrorCode.INVALID_TOOL_NAME


def test_loader_denies_when_permission_changes_before_load(
    model_config: ModelConfig,
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    provider = FakeSpecProvider(
        cards=(make_card(name="doc_search"),),
        specs={"doc_search": make_spec(name="doc_search")},
    )
    registry = DynamicToolRegistry(providers=(provider,))
    loader = ToolLoader(registry)
    lost_permission_context = AgentRuntimeContext(
        user_id="user_123",
        org_id="org_456",
        roles={"employee"},
        permission_scopes={"search:read"},
        connector_scopes={"google-drive": {"search:read"}},
        model_profile=model_config,
        trace_id="trace_lost",
        feature_flags={"dynamic_tool_loading"},
    )

    assert tuple(card.name for card in registry.list_tool_cards(runtime_context_admin)) == (
        "doc_search",
    )
    result = loader.load_tool(
        ToolLoadRequest(tool_name="doc_search", runtime_context=lost_permission_context)
    )

    assert result.error is not None
    assert result.error.code == ToolLoadErrorCode.PERMISSION_DENIED
    assert result.error.correlation_id == "trace_lost"
    assert provider.loaded_names == []


def test_loader_returns_typed_errors_for_disabled_malformed_and_connector_failures(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    disabled_loader = ToolLoader(
        DynamicToolRegistry(
            providers=(
                FakeSpecProvider(
                    cards=(make_card(name="disabled_tool", enabled=False),),
                    specs={},
                ),
            )
        )
    )
    malformed_loader = ToolLoader(
        DynamicToolRegistry(
            providers=(
                FakeSpecProvider(
                    cards=(make_card(name="doc_search"),),
                    specs={"doc_search": make_spec_dict(args_schema={"properties": {}})},
                ),
            )
        )
    )
    failing_loader = ToolLoader(
        DynamicToolRegistry(
            providers=(
                FakeSpecProvider(
                    cards=(make_card(name="doc_search"),),
                    specs={"doc_search": make_spec(name="doc_search")},
                    fail_on_load=True,
                ),
            )
        )
    )

    disabled_result = disabled_loader.load_tool(
        ToolLoadRequest(tool_name="disabled_tool", runtime_context=runtime_context_admin)
    )
    malformed_result = malformed_loader.load_tool(
        ToolLoadRequest(tool_name="doc_search", runtime_context=runtime_context_admin)
    )
    connector_result = failing_loader.load_tool(
        ToolLoadRequest(tool_name="doc_search", runtime_context=runtime_context_admin)
    )

    assert disabled_result.error is not None
    assert disabled_result.error.code == ToolLoadErrorCode.TOOL_DISABLED
    assert malformed_result.error is not None
    assert malformed_result.error.code == ToolLoadErrorCode.MALFORMED_TOOL_SPEC
    assert connector_result.error is not None
    assert connector_result.error.code == ToolLoadErrorCode.CONNECTOR_UNAVAILABLE
    assert connector_result.error.retryable is True
    assert "super-secret" not in connector_result.error.safe_message


def make_card(
    *,
    name: str = "doc_search",
    display_name: str = "Doc Search",
    short_description: str = "Search indexed Google Drive documents.",
    connector: str = "google-drive",
    tags: object = ("search", "docs"),
    required_scopes: object = ("docs:read",),
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW,
    load_cost: int = 10,
    enabled: bool = True,
) -> ToolCard:
    return ToolCard(
        name=name,
        display_name=display_name,
        short_description=short_description,
        connector=connector,
        tags=tags,
        required_scopes=required_scopes,
        risk_level=risk_level,
        load_cost=load_cost,
        enabled=enabled,
    )


def make_spec(
    *,
    name: str,
    args_schema: Mapping[str, object] | None = None,
    return_schema: Mapping[str, object] | None = None,
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW,
    side_effects: object = (ToolSideEffect.READ,),
) -> LoadedToolSpec:
    return LoadedToolSpec.model_validate(
        make_spec_dict(
            name=name,
            args_schema=args_schema,
            return_schema=return_schema,
            risk_level=risk_level,
            side_effects=side_effects,
        )
    )


def make_spec_dict(
    *,
    name: str = "doc_search",
    args_schema: Mapping[str, object] | None = None,
    return_schema: Mapping[str, object] | None = None,
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW,
    side_effects: object = (ToolSideEffect.READ,),
) -> dict[str, object]:
    return {
        "name": name,
        "description": "Use this tool to search indexed enterprise documents by query.",
        "args_schema": args_schema or {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "return_schema": return_schema or {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
        },
        "side_effects": side_effects,
        "timeout_ms": 5_000,
        "permission_policy": {
            "connector": "google-drive",
            "required_scopes": {"docs:read"},
            "risk_level": risk_level,
            "requires_confirmation": risk_level
            in {ToolRiskLevel.HIGH, ToolRiskLevel.CRITICAL},
        },
    }
