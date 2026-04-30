from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.capabilities.tools import (
    DynamicToolRegistry,
    LoadedToolSpec,
    ToolCard,
    ToolLoader,
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
