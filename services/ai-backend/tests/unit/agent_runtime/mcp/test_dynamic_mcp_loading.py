from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    ModelConfig,
    RuntimeErrorCode,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.capabilities.mcp import (
    CallMcpTool,
    DynamicMcpRegistry,
    McpAuthError,
    McpAuthMode,
    McpLoadErrorCode,
    McpLoadRequest,
    McpLoader,
    McpRiskLevel,
    McpServerHealth,
    McpTimeoutError,
    McpTransport,
)
from agent_runtime.capabilities.mcp.constants import Keys, Messages, Values
from agent_runtime.capabilities.mcp.middleware.dynamic_loader import LoadMcpServerTool
from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin


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

    async def test_registry_returns_only_authorized_healthy_cards(
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

        cards = await registry.list_server_cards(runtime_context_admin)

        assert tuple(card.name for card in cards) == (self.TestValues.Names.DRIVE_MCP,)

    async def test_registry_duplicate_names_raise_deterministic_error(
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
            await registry.list_server_cards(runtime_context_admin)

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
                    resources=(
                        self.make_resource(name=self.TestValues.Names.DRIVE_ROOT),
                    ),
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
        assert (
            result.loaded_server.connection_metadata.server_name
            == self.TestValues.Names.DRIVE_MCP
        )
        assert provider.created_clients == [self.TestValues.Names.DRIVE_MCP]

    async def test_loader_denies_when_permission_changes_before_load(
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

        assert tuple(
            card.name
            for card in await registry.list_server_cards(runtime_context_admin)
        ) == (self.TestValues.Names.DRIVE_MCP,)
        result = await loader.load_server(
            McpLoadRequest(
                server_name=self.TestValues.Names.DRIVE_MCP,
                runtime_context=lost_permission_context,
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
        timeout_result = asyncio.run(
            self.load_default(timeout_loader, runtime_context_admin)
        )
        unhealthy_result = asyncio.run(
            self.load_default(unhealthy_loader, runtime_context_admin)
        )

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
                                    self.make_tool(
                                        name=self.TestValues.Names.FIRST_TOOL
                                    ),
                                    self.make_tool(
                                        name=self.TestValues.Names.SECOND_TOOL
                                    ),
                                ),
                                resources=(),
                            )
                        },
                    ),
                )
            ),
            max_tool_descriptors=1,
        )

        malformed_result = asyncio.run(
            self.load_default(malformed_loader, runtime_context_admin)
        )
        duplicate_result = asyncio.run(
            self.load_default(duplicate_loader, runtime_context_admin)
        )
        collision_result = asyncio.run(
            collision_loader.load_server(
                McpLoadRequest(
                    server_name=self.TestValues.Names.DRIVE_MCP,
                    runtime_context=runtime_context_admin,
                    local_tool_names={self.TestValues.Names.LOCAL_SEARCH},
                )
            )
        )
        budget_result = asyncio.run(
            self.load_default(budget_loader, runtime_context_admin)
        )

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

    def test_loader_reports_invalid_local_tool_names(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        loader = self.make_loader(self.FakeMcpClient(tools=(), resources=()))

        result = asyncio.run(
            loader.load_server_by_name(
                server_name=self.TestValues.Names.DRIVE_MCP,
                runtime_context=runtime_context_admin,
                local_tool_names={"*"},
            )
        )

        assert result.error is not None
        assert result.error.code == McpLoadErrorCode.INVALID_LOCAL_TOOL_NAMES

    def test_load_mcp_server_tool_ignores_model_local_tool_names(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        loader = self.make_loader(
            self.FakeMcpClient(
                tools=(self.make_tool(name=self.TestValues.Names.DRIVE_SEARCH),),
                resources=(),
            )
        )
        tool = LoadMcpServerTool(
            loader=loader,
            runtime_context=runtime_context_admin,
        )

        result = asyncio.run(
            tool.ainvoke(
                {
                    "server_name": self.TestValues.Names.DRIVE_MCP,
                    "local_tool_names": ["*"],
                }
            )
        )

        assert (
            result["loaded_server"]["server_card"]["name"]
            == self.TestValues.Names.DRIVE_MCP
        )

    def test_call_mcp_tool_invokes_selected_loaded_tool(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        provider = self.FakeMcpProvider(
            cards=(self.make_card(name=self.TestValues.Names.DRIVE_MCP),),
            clients={
                self.TestValues.Names.DRIVE_MCP: self.FakeMcpClient(
                    tools=(self.make_tool(name=self.TestValues.Names.DRIVE_SEARCH),),
                    resources=(),
                    tool_outputs={
                        self.TestValues.Names.DRIVE_SEARCH: {
                            "content": [{"type": "text", "text": "found tasks"}]
                        }
                    },
                )
            },
        )
        registry = DynamicMcpRegistry(providers=(provider,))
        context_with_grant = runtime_context_admin.model_copy(
            update={
                "trace_metadata": {
                    "mcp_approval_grants": [
                        f"{self.TestValues.Names.DRIVE_MCP}:{self.TestValues.Names.DRIVE_SEARCH}"
                    ]
                }
            }
        )
        tool = CallMcpTool(
            registry=registry,
            loader=McpLoader(registry),
            runtime_context=context_with_grant,
        )

        result = asyncio.run(
            tool.ainvoke(
                {
                    "server_name": self.TestValues.Names.DRIVE_MCP,
                    "tool_name": self.TestValues.Names.DRIVE_SEARCH,
                    "arguments": {"query": "tasks"},
                }
            )
        )

        assert result["server_name"] == self.TestValues.Names.DRIVE_MCP
        assert result["tool_name"] == self.TestValues.Names.DRIVE_SEARCH
        assert result["output"]["content"][0]["text"] == "found tasks"
        assert self.TestValues.Names.DRIVE_MCP in provider.created_clients

    def test_call_mcp_tool_executes_after_native_approval(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        provider = self.FakeMcpProvider(
            cards=(self.make_card(name=self.TestValues.Names.DRIVE_MCP),),
            clients={
                self.TestValues.Names.DRIVE_MCP: self.FakeMcpClient(
                    tools=(self.make_tool(name=self.TestValues.Names.DRIVE_SEARCH),),
                    resources=(),
                )
            },
        )
        registry = DynamicMcpRegistry(providers=(provider,))
        tool = CallMcpTool(
            registry=registry,
            loader=McpLoader(registry),
            runtime_context=runtime_context_admin,
        )

        result = asyncio.run(
            tool.ainvoke(
                {
                    "server_name": self.TestValues.Names.DRIVE_MCP,
                    "tool_name": self.TestValues.Names.DRIVE_SEARCH,
                    "arguments": {"query": "tasks"},
                }
            )
        )

        assert result["server_name"] == self.TestValues.Names.DRIVE_MCP
        assert result["tool_name"] == self.TestValues.Names.DRIVE_SEARCH
        assert result["output"]["content"][0]["text"] == (
            "called drive_search with {'query': 'tasks'}"
        )

    def test_call_mcp_tool_collects_misplaced_tool_arguments(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        provider = self.FakeMcpProvider(
            cards=(self.make_card(name=self.TestValues.Names.DRIVE_MCP),),
            clients={
                self.TestValues.Names.DRIVE_MCP: self.FakeMcpClient(
                    tools=(self.make_tool(name=self.TestValues.Names.DRIVE_SEARCH),),
                    resources=(),
                )
            },
        )
        registry = DynamicMcpRegistry(providers=(provider,))
        tool = CallMcpTool(
            registry=registry,
            loader=McpLoader(registry),
            runtime_context=runtime_context_admin,
        )

        result = asyncio.run(
            tool.ainvoke(
                {
                    "server_name": self.TestValues.Names.DRIVE_MCP,
                    "tool_name": self.TestValues.Names.DRIVE_SEARCH,
                    "query": "tasks",
                    "assignees": ["me"],
                }
            )
        )

        assert result["output"]["content"][0]["text"] == (
            "called drive_search with {'query': 'tasks', 'assignees': ['me']}"
        )

    def test_call_mcp_tool_rejects_tool_not_returned_by_loaded_server(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        provider = self.FakeMcpProvider(
            cards=(self.make_card(name=self.TestValues.Names.DRIVE_MCP),),
            clients={
                self.TestValues.Names.DRIVE_MCP: self.FakeMcpClient(
                    tools=(self.make_tool(name=self.TestValues.Names.DRIVE_SEARCH),),
                    resources=(),
                )
            },
        )
        registry = DynamicMcpRegistry(providers=(provider,))
        tool = CallMcpTool(
            registry=registry,
            loader=McpLoader(registry),
            runtime_context=runtime_context_admin,
        )

        result = asyncio.run(
            tool.ainvoke(
                {
                    "server_name": self.TestValues.Names.DRIVE_MCP,
                    "tool_name": self.TestValues.Names.SECOND_TOOL,
                    "arguments": {},
                }
            )
        )

        assert "found tasks" not in str(result)
