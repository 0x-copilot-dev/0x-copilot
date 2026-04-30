from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.capabilities.mcp import (
    DynamicMcpRegistry,
    McpAuthMode,
    McpConnectionMetadata,
    McpLoadRequest,
    McpLoader,
    McpResourceAccessPolicy,
    McpResourceDescriptor,
    McpRiskLevel,
    McpServerCard,
    McpServerHealth,
    McpToolDescriptor,
    McpTransport,
)
from agent_runtime.capabilities.mcp.constants import Keys, Values


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
