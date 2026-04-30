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
    Messages,
    ToolCard,
    ToolLoadErrorCode,
    ToolLoadRequest,
    ToolLoader,
    ToolPermissionPolicy,
    ToolRiskLevel,
    ToolSideEffect,
)


class DynamicToolLoadingTestMixin:
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
                raise RuntimeError(DynamicToolLoadingTestMixin.Values.CONNECTOR_SECRET_ERROR)
            return self.specs[name]

    class Values:
        ANSWER_PROPERTY = "answer"
        CHAT_READ_SCOPE = "chat:read"
        CONNECTOR_GOOGLE_DRIVE = "google-drive"
        CONNECTOR_SECRET_ERROR = "connector token=super-secret"
        CONNECTOR_SLACK = "slack"
        DOC_SEARCH_DISPLAY_NAME = "Doc Search"
        DOC_SEARCH_NAME = "doc_search"
        DOC_SEARCH_SHORT_DESCRIPTION = "Search indexed Google Drive documents."
        DOC_SEARCH_SPEC_DESCRIPTION = (
            "Use this tool to search indexed enterprise documents by query."
        )
        DOCS_READ_SCOPE = "docs:read"
        DYNAMIC_TOOL_LOADING_FLAG = "dynamic_tool_loading"
        FIELD_ARGS_SCHEMA = "args_schema"
        FIELD_CONNECTOR = "connector"
        FIELD_DESCRIPTION = "description"
        FIELD_NAME = "name"
        FIELD_PERMISSION_POLICY = "permission_policy"
        FIELD_PROPERTIES = "properties"
        FIELD_REQUIRED = "required"
        FIELD_REQUIRED_SCOPES = "required_scopes"
        FIELD_REQUIRES_CONFIRMATION = "requires_confirmation"
        FIELD_RETURN_SCHEMA = "return_schema"
        FIELD_RISK_LEVEL = "risk_level"
        FIELD_SIDE_EFFECTS = "side_effects"
        FIELD_TIMEOUT_MS = "timeout_ms"
        FIELD_TYPE = "type"
        INVALID_DISPLAY_TOOL_NAME = "Doc Search"
        INVALID_TOOL_NAME = "Doc Search"
        JSON_OBJECT_TYPE = "object"
        JSON_STRING_TYPE = "string"
        MISSING_TOOL_NAME = "missing_tool"
        QUERY_PROPERTY = "query"
        SEARCH_READ_SCOPE = "search:read"
        SECRET_FRAGMENT = "super-secret"
        TOOL_DISABLED_NAME = "disabled_tool"
        TOOL_SLACK_SEARCH_NAME = "slack_search"
        TRACE_LOST = "trace_lost"
        USER_EMPLOYEE_ROLE = "employee"
        USER_ID = "user_123"
        ORG_ID = "org_456"
        RAW_DOC_SEARCH_NAME = "Doc_Search"
        RAW_DOCS_READ_SCOPE = "Docs:Read"
        RAW_GOOGLE_DRIVE_CONNECTOR = "Google-Drive"
        RAW_SEARCH_TAG = "Search"
        RAW_DOCS_TAG = "Docs"
        TOO_LONG_DESCRIPTION = "x" * 241

    def make_card(
        self,
        *,
        name: str = Values.DOC_SEARCH_NAME,
        display_name: str = Values.DOC_SEARCH_DISPLAY_NAME,
        short_description: str = Values.DOC_SEARCH_SHORT_DESCRIPTION,
        connector: str = Values.CONNECTOR_GOOGLE_DRIVE,
        tags: object = ("search", "docs"),
        required_scopes: object = (Values.DOCS_READ_SCOPE,),
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
        self,
        *,
        name: str,
        args_schema: Mapping[str, object] | None = None,
        return_schema: Mapping[str, object] | None = None,
        risk_level: ToolRiskLevel = ToolRiskLevel.LOW,
        side_effects: object = (ToolSideEffect.READ,),
    ) -> LoadedToolSpec:
        return LoadedToolSpec.model_validate(
            self.make_spec_dict(
                name=name,
                args_schema=args_schema,
                return_schema=return_schema,
                risk_level=risk_level,
                side_effects=side_effects,
            )
        )

    def make_spec_dict(
        self,
        *,
        name: str = Values.DOC_SEARCH_NAME,
        args_schema: Mapping[str, object] | None = None,
        return_schema: Mapping[str, object] | None = None,
        risk_level: ToolRiskLevel = ToolRiskLevel.LOW,
        side_effects: object = (ToolSideEffect.READ,),
    ) -> dict[str, object]:
        return {
            self.Values.FIELD_NAME: name,
            self.Values.FIELD_DESCRIPTION: self.Values.DOC_SEARCH_SPEC_DESCRIPTION,
            self.Values.FIELD_ARGS_SCHEMA: args_schema or self.make_args_schema(),
            self.Values.FIELD_RETURN_SCHEMA: return_schema or self.make_return_schema(),
            self.Values.FIELD_SIDE_EFFECTS: side_effects,
            self.Values.FIELD_TIMEOUT_MS: 5_000,
            self.Values.FIELD_PERMISSION_POLICY: {
                self.Values.FIELD_CONNECTOR: self.Values.CONNECTOR_GOOGLE_DRIVE,
                self.Values.FIELD_REQUIRED_SCOPES: {self.Values.DOCS_READ_SCOPE},
                self.Values.FIELD_RISK_LEVEL: risk_level,
                self.Values.FIELD_REQUIRES_CONFIRMATION: risk_level
                in {ToolRiskLevel.HIGH, ToolRiskLevel.CRITICAL},
            },
        }

    def make_args_schema(self) -> dict[str, object]:
        return {
            self.Values.FIELD_TYPE: self.Values.JSON_OBJECT_TYPE,
            self.Values.FIELD_PROPERTIES: {
                self.Values.QUERY_PROPERTY: {self.Values.FIELD_TYPE: self.Values.JSON_STRING_TYPE}
            },
            self.Values.FIELD_REQUIRED: [self.Values.QUERY_PROPERTY],
        }

    def make_return_schema(self) -> dict[str, object]:
        return {
            self.Values.FIELD_TYPE: self.Values.JSON_OBJECT_TYPE,
            self.Values.FIELD_PROPERTIES: {
                self.Values.ANSWER_PROPERTY: {self.Values.FIELD_TYPE: self.Values.JSON_STRING_TYPE}
            },
        }

    def make_malformed_schema(self) -> dict[str, object]:
        return {self.Values.FIELD_PROPERTIES: {}}

    def make_provider(
        self,
        *,
        cards: Sequence[ToolCard | Mapping[str, object]],
        specs: Mapping[str, LoadedToolSpec | Mapping[str, object]],
        fail_on_load: bool = False,
    ) -> FakeSpecProvider:
        return self.FakeSpecProvider(
            cards=cards,
            specs=specs,
            fail_on_load=fail_on_load,
        )

    def make_loader(self, provider: FakeSpecProvider) -> ToolLoader:
        return ToolLoader(DynamicToolRegistry(providers=(provider,)))

    def make_lost_permission_context(self, model_config: ModelConfig) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id=self.Values.USER_ID,
            org_id=self.Values.ORG_ID,
            roles={self.Values.USER_EMPLOYEE_ROLE},
            permission_scopes={self.Values.SEARCH_READ_SCOPE},
            connector_scopes={
                self.Values.CONNECTOR_GOOGLE_DRIVE: {self.Values.SEARCH_READ_SCOPE}
            },
            model_profile=model_config,
            trace_id=self.Values.TRACE_LOST,
            feature_flags={self.Values.DYNAMIC_TOOL_LOADING_FLAG},
        )


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
