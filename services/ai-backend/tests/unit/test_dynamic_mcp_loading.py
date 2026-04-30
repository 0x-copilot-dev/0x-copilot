from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError

from agent_runtime.agent.contracts import AgentRuntimeContext, ModelConfig, RuntimeErrorCode
from agent_runtime.agent.errors import AgentRuntimeError
from agent_runtime.mcp import (
    DynamicMcpRegistry,
    McpAuthError,
    McpAuthMode,
    McpConnectionMetadata,
    McpLoadErrorCode,
    McpLoadRequest,
    McpLoader,
    McpResourceAccessPolicy,
    McpResourceDescriptor,
    McpRiskLevel,
    McpServerCard,
    McpServerHealth,
    McpTimeoutError,
    McpToolDescriptor,
    McpTransport,
)
from agent_runtime.mcp.constants import Keys, Messages, Values


class DynamicMcpLoadingMixin:
    """Shared fakes and factories for dynamic MCP loading tests."""

    class TestValues:
        class Descriptions:
            CARD = "Search Google Drive through MCP."
            RESOURCE = "Root Drive resource index."
            TOOL = "Search indexed Google Drive documents."

        class Ids:
            LOST_TRACE = "trace_lost"
            ORG_OTHER = "org_other"
            USER_123 = "user_123"
            ORG_456 = "org_456"

        class Mime:
            JSON = "application/json"

        class Names:
            ANSWER = "answer"
            DISABLED_MCP = "disabled_mcp"
            DISPLAY_CARD = "Drive_MCP"
            DISPLAY_REQUEST = "Drive MCP"
            DRIVE_MCP = "drive_mcp"
            DRIVE_ROOT = "Drive Root"
            DRIVE_SEARCH = "drive_search"
            FIRST_TOOL = "first_tool"
            LOCAL_SEARCH = "local_search"
            OFFLINE_MCP = "offline_mcp"
            OTHER_ORG_MCP = "other_org_mcp"
            SECOND_TOOL = "second_tool"
            SLACK_MCP = "slack_mcp"

        class Roles:
            EMPLOYEE = "employee"

        class Scopes:
            CHAT_READ = "chat:read"
            DOCS_READ = "docs:read"
            DOCS_READ_DISPLAY = "Docs:Read"
            SEARCH_READ = "search:read"

        class Secrets:
            SLOW = "slow"
            TOKEN = "token=super-secret"

        class Transports:
            FTP = "ftp"

        class Uris:
            FILE = "file:///etc/passwd"
            HTTPS_ROOT = "https://docs.example.com/root"
            MCP_ROOT = "mcp://drive/docs/root"

        class FeatureFlags:
            DYNAMIC_MCP_LOADING = "dynamic_mcp_loading"

    @dataclass
    class FakeMcpClient:
        tools: Sequence[McpToolDescriptor | Mapping[str, object]]
        resources: Sequence[McpResourceDescriptor | Mapping[str, object]]
        metadata: McpConnectionMetadata | Mapping[str, object] | None = None
        connect_error: Exception | None = None
        list_tools_error: Exception | None = None

        async def connect(self) -> McpConnectionMetadata | Mapping[str, object] | None:
            if self.connect_error is not None:
                raise self.connect_error
            return self.metadata

        async def list_tools(self) -> Sequence[McpToolDescriptor | Mapping[str, object]]:
            if self.list_tools_error is not None:
                raise self.list_tools_error
            return self.tools

        async def list_resources(self) -> Sequence[McpResourceDescriptor | Mapping[str, object]]:
            return self.resources

    @dataclass
    class FakeMcpProvider:
        cards: Sequence[McpServerCard | Mapping[str, object]]
        clients: Mapping[str, "DynamicMcpLoadingMixin.FakeMcpClient"]
        created_clients: list[str] = field(default_factory=list)

        def list_server_cards(self) -> Sequence[McpServerCard | Mapping[str, object]]:
            return self.cards

        def create_client(self, card: McpServerCard) -> "DynamicMcpLoadingMixin.FakeMcpClient":
            self.created_clients.append(card.name)
            return self.clients[card.name]

    def make_card(
        self,
        *,
        name: str | None = None,
        short_description: str | None = None,
        transport: McpTransport | str = McpTransport.HTTP,
        auth_mode: McpAuthMode | str = McpAuthMode.OAUTH2,
        required_scopes: object | None = None,
        health: McpServerHealth = McpServerHealth.HEALTHY,
        load_cost: int = 10,
        enabled: bool = True,
        allowed_org_ids: object = (),
        allowed_user_ids: object = (),
    ) -> McpServerCard:
        return McpServerCard(
            name=name or self.TestValues.Names.DRIVE_MCP,
            short_description=short_description or self.TestValues.Descriptions.CARD,
            transport=transport,
            auth_mode=auth_mode,
            required_scopes=required_scopes or (self.TestValues.Scopes.DOCS_READ,),
            health=health,
            load_cost=load_cost,
            enabled=enabled,
            allowed_org_ids=allowed_org_ids,
            allowed_user_ids=allowed_user_ids,
        )

    def make_tool(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        input_schema: Mapping[str, object] | None = None,
        output_shape: Mapping[str, object] | None = None,
        risk_level: McpRiskLevel = McpRiskLevel.LOW,
    ) -> McpToolDescriptor:
        return McpToolDescriptor(
            name=name or self.TestValues.Names.DRIVE_SEARCH,
            description=description or self.TestValues.Descriptions.TOOL,
            input_schema=input_schema or self.object_query_schema(),
            output_shape=output_shape or self.object_answer_schema(),
            risk_level=risk_level,
        )

    def make_resource(
        self,
        *,
        uri: str | None = None,
        name: str | None = None,
        mime_type: str | None = None,
        description: str | None = None,
    ) -> McpResourceDescriptor:
        return McpResourceDescriptor(
            uri=uri or self.TestValues.Uris.HTTPS_ROOT,
            name=name or self.TestValues.Names.DRIVE_ROOT,
            mime_type=mime_type or self.TestValues.Mime.JSON,
            description=description or self.TestValues.Descriptions.RESOURCE,
            access_policy=McpResourceAccessPolicy(
                required_scopes={self.TestValues.Scopes.DOCS_READ}
            ),
        )

    def make_loader(self, client: FakeMcpClient) -> McpLoader:
        return McpLoader(
            DynamicMcpRegistry(
                providers=(
                    self.FakeMcpProvider(
                        cards=(self.make_card(name=self.TestValues.Names.DRIVE_MCP),),
                        clients={self.TestValues.Names.DRIVE_MCP: client},
                    ),
                )
            )
        )

    def load_default(
        self,
        loader: McpLoader,
        runtime_context: AgentRuntimeContext,
    ):
        return loader.load_server(
            McpLoadRequest(
                server_name=self.TestValues.Names.DRIVE_MCP,
                runtime_context=runtime_context,
            )
        )

    def object_query_schema(self) -> Mapping[str, object]:
        return {
            Keys.Schema.TYPE: Values.SchemaType.OBJECT,
            Keys.Schema.PROPERTIES: {
                Keys.Schema.QUERY: {Keys.Schema.TYPE: Values.SchemaType.STRING}
            },
            Keys.Schema.REQUIRED: [Keys.Schema.QUERY],
        }

    def object_answer_schema(self) -> Mapping[str, object]:
        return {
            Keys.Schema.TYPE: Values.SchemaType.OBJECT,
            Keys.Schema.PROPERTIES: {
                self.TestValues.Names.ANSWER: {Keys.Schema.TYPE: Values.SchemaType.STRING}
            },
        }

    def malformed_schema(self) -> Mapping[str, object]:
        return {Keys.Schema.PROPERTIES: {}}

    def malformed_tool_payload(self) -> Mapping[str, object]:
        return {
            Keys.Field.NAME: self.TestValues.Names.DRIVE_SEARCH,
            Keys.Field.DESCRIPTION: self.TestValues.Descriptions.TOOL,
            Keys.Field.INPUT_SCHEMA: self.malformed_schema(),
            Keys.Field.OUTPUT_SHAPE: {Keys.Schema.TYPE: Values.SchemaType.OBJECT},
            Keys.Field.RISK_LEVEL: Values.Risk.LOW,
        }

    def lost_permission_context(self, model_config: ModelConfig) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id=self.TestValues.Ids.USER_123,
            org_id=self.TestValues.Ids.ORG_456,
            roles={self.TestValues.Roles.EMPLOYEE},
            permission_scopes={self.TestValues.Scopes.SEARCH_READ},
            model_profile=model_config,
            trace_id=self.TestValues.Ids.LOST_TRACE,
            feature_flags={self.TestValues.FeatureFlags.DYNAMIC_MCP_LOADING},
        )


class TestDynamicMcpLoading(DynamicMcpLoadingMixin):
    def test_mcp_server_card_normalizes_visibility_metadata(self) -> None:
        card = self.make_card(
            name=self.TestValues.Names.DISPLAY_CARD,
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            required_scopes={self.TestValues.Scopes.DOCS_READ_DISPLAY},
            allowed_org_ids={self.TestValues.Ids.ORG_456},
        )

        assert card.name == self.TestValues.Names.DRIVE_MCP
        assert card.transport == McpTransport.HTTP
        assert card.auth_mode == McpAuthMode.OAUTH2
        assert card.required_scopes == frozenset({self.TestValues.Scopes.DOCS_READ})
        assert card.allowed_org_ids == frozenset({self.TestValues.Ids.ORG_456})

        with pytest.raises(ValidationError):
            self.make_card(name=self.TestValues.Names.DISPLAY_REQUEST)

        with pytest.raises(ValidationError):
            self.make_card(transport=self.TestValues.Transports.FTP)

    def test_mcp_descriptors_validate_schemas_and_resource_uri(self) -> None:
        tool = self.make_tool(
            name=self.TestValues.Names.DRIVE_SEARCH,
            risk_level=McpRiskLevel.MEDIUM,
        )
        resource = self.make_resource(uri=self.TestValues.Uris.MCP_ROOT)

        assert tool.input_schema[Keys.Schema.TYPE] == Values.SchemaType.OBJECT
        assert tool.risk_level == McpRiskLevel.MEDIUM
        assert resource.access_policy.required_scopes == frozenset(
            {self.TestValues.Scopes.DOCS_READ}
        )

        with pytest.raises(ValidationError):
            self.make_tool(
                name=self.TestValues.Names.DRIVE_SEARCH,
                description=" ",
            )

        with pytest.raises(ValidationError):
            self.make_tool(
                name=self.TestValues.Names.DRIVE_SEARCH,
                input_schema=self.malformed_schema(),
            )

        with pytest.raises(ValidationError):
            self.make_resource(uri=self.TestValues.Uris.FILE)

    def test_registry_returns_only_authorized_healthy_cards(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        provider = self.FakeMcpProvider(
            cards=(
                self.make_card(name=self.TestValues.Names.DRIVE_MCP),
                self.make_card(
                    name=self.TestValues.Names.SLACK_MCP,
                    required_scopes={self.TestValues.Scopes.CHAT_READ},
                ),
                self.make_card(
                    name=self.TestValues.Names.OFFLINE_MCP,
                    health=McpServerHealth.UNAVAILABLE,
                ),
                self.make_card(
                    name=self.TestValues.Names.DISABLED_MCP,
                    enabled=False,
                ),
                self.make_card(
                    name=self.TestValues.Names.OTHER_ORG_MCP,
                    allowed_org_ids={self.TestValues.Ids.ORG_OTHER},
                ),
            ),
            clients={},
        )
        registry = DynamicMcpRegistry(providers=(provider,))

        cards = registry.list_server_cards(runtime_context_admin)

        assert tuple(card.name for card in cards) == (self.TestValues.Names.DRIVE_MCP,)

    def test_registry_duplicate_names_raise_deterministic_error(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        provider = self.FakeMcpProvider(
            cards=(
                self.make_card(name=self.TestValues.Names.DRIVE_MCP),
                self.make_card(name=self.TestValues.Names.DRIVE_MCP),
            ),
            clients={},
        )
        registry = DynamicMcpRegistry(providers=(provider,))

        with pytest.raises(AgentRuntimeError) as exc_info:
            registry.list_server_cards(runtime_context_admin)

        assert exc_info.value.code == RuntimeErrorCode.CONFIGURATION_ERROR
        assert exc_info.value.safe_message == Messages.Registry.DUPLICATE_SERVER_NAME

    def test_loader_returns_validated_descriptors_after_permission_recheck(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        provider = self.FakeMcpProvider(
            cards=(self.make_card(name=self.TestValues.Names.DRIVE_MCP),),
            clients={
                self.TestValues.Names.DRIVE_MCP: self.FakeMcpClient(
                    tools=(self.make_tool(name=self.TestValues.Names.DRIVE_SEARCH),),
                    resources=(self.make_resource(name=self.TestValues.Names.DRIVE_ROOT),),
                )
            },
        )
        loader = McpLoader(DynamicMcpRegistry(providers=(provider,)))

        result = asyncio.run(
            loader.load_server(
                McpLoadRequest(
                    server_name=self.TestValues.Names.DRIVE_MCP,
                    runtime_context=runtime_context_admin,
                )
            )
        )

        assert result.succeeded
        assert result.loaded_server is not None
        assert tuple(tool.name for tool in result.loaded_server.tools) == (
            self.TestValues.Names.DRIVE_SEARCH,
        )
        assert tuple(resource.name for resource in result.loaded_server.resources) == (
            self.TestValues.Names.DRIVE_ROOT,
        )
        assert result.loaded_server.connection_metadata.server_name == self.TestValues.Names.DRIVE_MCP
        assert provider.created_clients == [self.TestValues.Names.DRIVE_MCP]

    def test_loader_denies_when_permission_changes_before_load(
        self,
        model_config: ModelConfig,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        provider = self.FakeMcpProvider(
            cards=(self.make_card(name=self.TestValues.Names.DRIVE_MCP),),
            clients={
                self.TestValues.Names.DRIVE_MCP: self.FakeMcpClient(
                    tools=(),
                    resources=(),
                )
            },
        )
        registry = DynamicMcpRegistry(providers=(provider,))
        loader = McpLoader(registry)
        lost_permission_context = self.lost_permission_context(model_config)

        assert tuple(card.name for card in registry.list_server_cards(runtime_context_admin)) == (
            self.TestValues.Names.DRIVE_MCP,
        )
        result = asyncio.run(
            loader.load_server(
                McpLoadRequest(
                    server_name=self.TestValues.Names.DRIVE_MCP,
                    runtime_context=lost_permission_context,
                )
            )
        )

        assert result.error is not None
        assert result.error.code == McpLoadErrorCode.PERMISSION_DENIED
        assert result.error.correlation_id == self.TestValues.Ids.LOST_TRACE
        assert provider.created_clients == []

    def test_loader_returns_typed_errors_for_auth_timeout_and_unhealthy_server(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        auth_loader = self.make_loader(
            self.FakeMcpClient(
                tools=(),
                resources=(),
                connect_error=McpAuthError(self.TestValues.Secrets.TOKEN),
            )
        )
        timeout_loader = self.make_loader(
            self.FakeMcpClient(
                tools=(),
                resources=(),
                connect_error=McpTimeoutError(self.TestValues.Secrets.SLOW),
            )
        )
        unhealthy_loader = McpLoader(
            DynamicMcpRegistry(
                providers=(
                    self.FakeMcpProvider(
                        cards=(
                            self.make_card(
                                name=self.TestValues.Names.DRIVE_MCP,
                                health=McpServerHealth.UNAVAILABLE,
                            ),
                        ),
                        clients={
                            self.TestValues.Names.DRIVE_MCP: self.FakeMcpClient(
                                tools=(),
                                resources=(),
                            )
                        },
                    ),
                )
            )
        )

        auth_result = asyncio.run(self.load_default(auth_loader, runtime_context_admin))
        timeout_result = asyncio.run(self.load_default(timeout_loader, runtime_context_admin))
        unhealthy_result = asyncio.run(self.load_default(unhealthy_loader, runtime_context_admin))

        assert auth_result.error is not None
        assert auth_result.error.code == McpLoadErrorCode.AUTH_FAILURE
        assert self.TestValues.Secrets.TOKEN not in auth_result.error.safe_message
        assert timeout_result.error is not None
        assert timeout_result.error.code == McpLoadErrorCode.TIMEOUT
        assert timeout_result.error.retryable is True
        assert unhealthy_result.error is not None
        assert unhealthy_result.error.code == McpLoadErrorCode.SERVER_UNHEALTHY

    def test_loader_rejects_malformed_duplicate_collision_and_over_budget_descriptors(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        malformed_loader = self.make_loader(
            self.FakeMcpClient(
                tools=(self.malformed_tool_payload(),),
                resources=(),
            )
        )
        duplicate_loader = self.make_loader(
            self.FakeMcpClient(
                tools=(
                    self.make_tool(name=self.TestValues.Names.DRIVE_SEARCH),
                    self.make_tool(name=self.TestValues.Names.DRIVE_SEARCH),
                ),
                resources=(),
            )
        )
        collision_loader = self.make_loader(
            self.FakeMcpClient(
                tools=(self.make_tool(name=self.TestValues.Names.LOCAL_SEARCH),),
                resources=(),
            )
        )
        budget_loader = McpLoader(
            DynamicMcpRegistry(
                providers=(
                    self.FakeMcpProvider(
                        cards=(self.make_card(name=self.TestValues.Names.DRIVE_MCP),),
                        clients={
                            self.TestValues.Names.DRIVE_MCP: self.FakeMcpClient(
                                tools=(
                                    self.make_tool(name=self.TestValues.Names.FIRST_TOOL),
                                    self.make_tool(name=self.TestValues.Names.SECOND_TOOL),
                                ),
                                resources=(),
                            )
                        },
                    ),
                )
            ),
            max_tool_descriptors=1,
        )

        malformed_result = asyncio.run(self.load_default(malformed_loader, runtime_context_admin))
        duplicate_result = asyncio.run(self.load_default(duplicate_loader, runtime_context_admin))
        collision_result = asyncio.run(
            collision_loader.load_server(
                McpLoadRequest(
                    server_name=self.TestValues.Names.DRIVE_MCP,
                    runtime_context=runtime_context_admin,
                    local_tool_names={self.TestValues.Names.LOCAL_SEARCH},
                )
            )
        )
        budget_result = asyncio.run(self.load_default(budget_loader, runtime_context_admin))

        assert malformed_result.error is not None
        assert malformed_result.error.code == McpLoadErrorCode.MALFORMED_DESCRIPTOR
        assert duplicate_result.error is not None
        assert duplicate_result.error.code == McpLoadErrorCode.DUPLICATE_DESCRIPTOR_NAME
        assert collision_result.error is not None
        assert collision_result.error.code == McpLoadErrorCode.LOCAL_TOOL_COLLISION
        assert budget_result.error is not None
        assert budget_result.error.code == McpLoadErrorCode.LOAD_BUDGET_EXCEEDED

    def test_loader_rejects_display_name_requests(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        loader = self.make_loader(self.FakeMcpClient(tools=(), resources=()))

        result = asyncio.run(
            loader.load_server_by_name(
                server_name=self.TestValues.Names.DISPLAY_REQUEST,
                runtime_context=runtime_context_admin,
            )
        )

        assert result.error is not None
        assert result.error.code == McpLoadErrorCode.INVALID_SERVER_NAME
