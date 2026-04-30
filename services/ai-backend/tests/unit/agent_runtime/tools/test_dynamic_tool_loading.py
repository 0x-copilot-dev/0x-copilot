from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig, RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.capabilities.tools import (
    DynamicToolRegistry,
    Messages,
    ToolLoadErrorCode,
    ToolLoadRequest,
    ToolLoader,
    ToolPermissionPolicy,
    ToolRiskLevel,
    ToolSideEffect,
)
from tests.unit.agent_runtime.tools.helpers import DynamicToolLoadingTestMixin


class TestDynamicToolLoading(DynamicToolLoadingTestMixin):
    def test_tool_card_normalizes_compact_metadata(self) -> None:
        card = self.make_card(
            name=self.Values.RAW_DOC_SEARCH_NAME,
            connector=self.Values.RAW_GOOGLE_DRIVE_CONNECTOR,
            tags={self.Values.RAW_DOCS_TAG, self.Values.RAW_SEARCH_TAG},
            required_scopes={self.Values.RAW_DOCS_READ_SCOPE},
        )

        assert card.name == self.Values.DOC_SEARCH_NAME
        assert card.connector == self.Values.CONNECTOR_GOOGLE_DRIVE
        assert card.tags == frozenset({"docs", "search"})
        assert card.required_scopes == frozenset({self.Values.DOCS_READ_SCOPE})

    def test_tool_card_rejects_bad_slug_and_oversized_description(self) -> None:
        with pytest.raises(ValidationError):
            self.make_card(name=self.Values.INVALID_TOOL_NAME)

        with pytest.raises(ValidationError):
            self.make_card(short_description=self.Values.TOO_LONG_DESCRIPTION)

    def test_loaded_tool_spec_validates_schema_and_risk_policy(self) -> None:
        spec = self.make_spec(
            name=self.Values.DOC_SEARCH_NAME,
            risk_level=ToolRiskLevel.MEDIUM,
            side_effects={ToolSideEffect.READ, ToolSideEffect.EXTERNAL_CALL},
        )

        assert spec.args_schema[self.Values.FIELD_TYPE] == self.Values.JSON_OBJECT_TYPE
        assert spec.permission_policy.risk_level == ToolRiskLevel.MEDIUM

        with pytest.raises(ValidationError):
            self.make_spec(
                name=self.Values.DOC_SEARCH_NAME,
                args_schema=self.make_malformed_schema(),
            )

        with pytest.raises(ValidationError):
            ToolPermissionPolicy(
                connector=self.Values.CONNECTOR_GOOGLE_DRIVE,
                required_scopes={self.Values.DOCS_READ_SCOPE},
                risk_level=ToolRiskLevel.HIGH,
                requires_confirmation=False,
            )

    def test_registry_returns_only_authorized_cards(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        provider = self.make_provider(
            cards=(
                self.make_card(name=self.Values.DOC_SEARCH_NAME),
                self.make_card(
                    name=self.Values.TOOL_SLACK_SEARCH_NAME,
                    connector=self.Values.CONNECTOR_SLACK,
                    required_scopes={self.Values.CHAT_READ_SCOPE},
                ),
                self.make_card(name=self.Values.TOOL_DISABLED_NAME, enabled=False),
            ),
            specs={},
        )
        registry = DynamicToolRegistry(providers=(provider,))

        cards = registry.list_tool_cards(runtime_context_admin)

        assert tuple(card.name for card in cards) == (self.Values.DOC_SEARCH_NAME,)

    def test_registry_duplicate_names_raise_deterministic_error(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        provider = self.make_provider(
            cards=(
                self.make_card(
                    name=self.Values.DOC_SEARCH_NAME,
                    connector=self.Values.CONNECTOR_GOOGLE_DRIVE,
                ),
                self.make_card(
                    name=self.Values.DOC_SEARCH_NAME,
                    connector=self.Values.CONNECTOR_SLACK,
                ),
            ),
            specs={},
        )
        registry = DynamicToolRegistry(providers=(provider,))

        with pytest.raises(AgentRuntimeError) as exc_info:
            registry.list_tool_cards(runtime_context_admin)

        assert exc_info.value.code == RuntimeErrorCode.CONFIGURATION_ERROR
        assert exc_info.value.safe_message == Messages.Errors.DUPLICATE_TOOL_REGISTRATION

    def test_loader_returns_validated_spec_after_permission_recheck(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        spec = self.make_spec(name=self.Values.DOC_SEARCH_NAME)
        provider = self.make_provider(
            cards=(self.make_card(name=self.Values.DOC_SEARCH_NAME),),
            specs={self.Values.DOC_SEARCH_NAME: spec},
        )
        loader = self.make_loader(provider)

        result = loader.load_tool(
            ToolLoadRequest(
                tool_name=self.Values.DOC_SEARCH_NAME,
                runtime_context=runtime_context_admin,
            )
        )

        assert result.succeeded
        assert result.loaded_spec == spec
        assert provider.loaded_names == [self.Values.DOC_SEARCH_NAME]

    def test_loader_rejects_unknown_duplicate_and_display_name_requests(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        provider = self.make_provider(
            cards=(
                self.make_card(
                    name=self.Values.DOC_SEARCH_NAME,
                    connector=self.Values.CONNECTOR_GOOGLE_DRIVE,
                ),
                self.make_card(
                    name=self.Values.DOC_SEARCH_NAME,
                    connector=self.Values.CONNECTOR_SLACK,
                ),
            ),
            specs={},
        )
        loader = self.make_loader(provider)

        duplicate_result = loader.load_tool(
            ToolLoadRequest(
                tool_name=self.Values.DOC_SEARCH_NAME,
                runtime_context=runtime_context_admin,
            )
        )
        unknown_result = loader.load_tool(
            ToolLoadRequest(
                tool_name=self.Values.MISSING_TOOL_NAME,
                runtime_context=runtime_context_admin,
            )
        )
        display_name_result = loader.load_tool_by_name(
            tool_name=self.Values.INVALID_DISPLAY_TOOL_NAME,
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
        self,
        model_config: ModelConfig,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        provider = self.make_provider(
            cards=(self.make_card(name=self.Values.DOC_SEARCH_NAME),),
            specs={
                self.Values.DOC_SEARCH_NAME: self.make_spec(name=self.Values.DOC_SEARCH_NAME)
            },
        )
        registry = DynamicToolRegistry(providers=(provider,))
        loader = ToolLoader(registry)
        lost_permission_context = self.make_lost_permission_context(model_config)

        assert tuple(card.name for card in registry.list_tool_cards(runtime_context_admin)) == (
            self.Values.DOC_SEARCH_NAME,
        )
        result = loader.load_tool(
            ToolLoadRequest(
                tool_name=self.Values.DOC_SEARCH_NAME,
                runtime_context=lost_permission_context,
            )
        )

        assert result.error is not None
        assert result.error.code == ToolLoadErrorCode.PERMISSION_DENIED
        assert result.error.correlation_id == self.Values.TRACE_LOST
        assert provider.loaded_names == []

    def test_loader_returns_typed_errors_for_disabled_malformed_and_connector_failures(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        disabled_loader = self.make_loader(
            self.make_provider(
                cards=(self.make_card(name=self.Values.TOOL_DISABLED_NAME, enabled=False),),
                specs={},
            )
        )
        malformed_loader = self.make_loader(
            self.make_provider(
                cards=(self.make_card(name=self.Values.DOC_SEARCH_NAME),),
                specs={
                    self.Values.DOC_SEARCH_NAME: self.make_spec_dict(
                        args_schema=self.make_malformed_schema()
                    )
                },
            )
        )
        failing_loader = self.make_loader(
            self.make_provider(
                cards=(self.make_card(name=self.Values.DOC_SEARCH_NAME),),
                specs={
                    self.Values.DOC_SEARCH_NAME: self.make_spec(
                        name=self.Values.DOC_SEARCH_NAME
                    )
                },
                fail_on_load=True,
            )
        )

        disabled_result = disabled_loader.load_tool(
            ToolLoadRequest(
                tool_name=self.Values.TOOL_DISABLED_NAME,
                runtime_context=runtime_context_admin,
            )
        )
        malformed_result = malformed_loader.load_tool(
            ToolLoadRequest(
                tool_name=self.Values.DOC_SEARCH_NAME,
                runtime_context=runtime_context_admin,
            )
        )
        connector_result = failing_loader.load_tool(
            ToolLoadRequest(
                tool_name=self.Values.DOC_SEARCH_NAME,
                runtime_context=runtime_context_admin,
            )
        )

        assert disabled_result.error is not None
        assert disabled_result.error.code == ToolLoadErrorCode.TOOL_DISABLED
        assert malformed_result.error is not None
        assert malformed_result.error.code == ToolLoadErrorCode.MALFORMED_TOOL_SPEC
        assert connector_result.error is not None
        assert connector_result.error.code == ToolLoadErrorCode.CONNECTOR_UNAVAILABLE
        assert connector_result.error.retryable is True
        assert self.Values.SECRET_FRAGMENT not in connector_result.error.safe_message
