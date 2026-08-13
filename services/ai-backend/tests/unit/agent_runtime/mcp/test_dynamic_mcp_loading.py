from __future__ import annotations

import asyncio
import logging

import pytest
from pydantic import ValidationError

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    ModelConfig,
    RuntimeErrorCode,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.capabilities.mcp import (
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
from agent_runtime.capabilities.mcp.client import McpConnectionError
from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin


class TestDynamicMcpLoading(DynamicMcpLoadingMixin):
    def test_loader_closes_closeable_clients_after_success_and_error(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        success_client = self.FakeMcpClient(
            tools=(self.make_tool(),), resources=(self.make_resource(),)
        )
        success_closes: list[bool] = []

        async def close_success(*, cancel: bool = False) -> None:
            success_closes.append(cancel)

        success_client.aclose = close_success  # type: ignore[attr-defined]
        assert asyncio.run(
            self.make_loader(success_client).load_server(
                McpLoadRequest(
                    server_name=self.TestValues.Names.DRIVE_MCP,
                    runtime_context=runtime_context_admin,
                )
            )
        ).succeeded
        assert success_closes == [False]

        failing_client = self.FakeMcpClient(
            tools=(),
            resources=(),
            connect_error=McpConnectionError("offline"),
        )
        failing_closes: list[bool] = []

        async def close_failure(*, cancel: bool = False) -> None:
            failing_closes.append(cancel)

        failing_client.aclose = close_failure  # type: ignore[attr-defined]
        result = asyncio.run(
            self.make_loader(failing_client).load_server(
                McpLoadRequest(
                    server_name=self.TestValues.Names.DRIVE_MCP,
                    runtime_context=runtime_context_admin,
                )
            )
        )
        assert result.succeeded is False
        assert failing_closes == [True]

        cleanup_failure_client = self.FakeMcpClient(
            tools=(self.make_tool(),), resources=(self.make_resource(),)
        )

        async def close_raises(*, cancel: bool = False) -> None:
            del cancel
            raise RuntimeError("release failed")

        cleanup_failure_client.aclose = close_raises  # type: ignore[attr-defined]
        cleanup_failure_result = asyncio.run(
            self.make_loader(cleanup_failure_client).load_server(
                McpLoadRequest(
                    server_name=self.TestValues.Names.DRIVE_MCP,
                    runtime_context=runtime_context_admin,
                )
            )
        )
        assert cleanup_failure_result.succeeded

        primary_and_cleanup_failure_client = self.FakeMcpClient(
            tools=(),
            resources=(),
            connect_error=McpConnectionError("offline"),
        )
        primary_and_cleanup_failure_client.aclose = close_raises  # type: ignore[attr-defined]
        primary_and_cleanup_failure_result = asyncio.run(
            self.make_loader(primary_and_cleanup_failure_client).load_server(
                McpLoadRequest(
                    server_name=self.TestValues.Names.DRIVE_MCP,
                    runtime_context=runtime_context_admin,
                )
            )
        )
        assert primary_and_cleanup_failure_result.succeeded is False

    async def test_loader_cancels_closeable_client_on_task_cancellation(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        client = self.FakeMcpClient(tools=(), resources=())
        closes: list[bool] = []

        async def connect() -> None:
            started.set()
            await release.wait()

        async def close(*, cancel: bool = False) -> None:
            closes.append(cancel)

        client.connect = connect  # type: ignore[method-assign]
        client.aclose = close  # type: ignore[attr-defined]
        task = asyncio.create_task(
            self.make_loader(client).load_server(
                McpLoadRequest(
                    server_name=self.TestValues.Names.DRIVE_MCP,
                    runtime_context=runtime_context_admin,
                )
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closes == [True]

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

        # A schema that is not an object at all cannot be repaired into one.
        with pytest.raises(ValidationError):
            self.make_tool(
                name=self.TestValues.Names.DRIVE_SEARCH,
                input_schema=self.malformed_schema(),
            )

        # ...but a *typeless* schema is repaired, not rejected. This assertion
        # replaces one that required the opposite: the old suite treated a
        # missing top-level ``type`` as malformed and dropped the tool, which
        # is the bug -- real connectors ship this shape constantly.
        repaired = self.make_tool(
            name=self.TestValues.Names.DRIVE_SEARCH,
            input_schema=self.typeless_schema(),
        )
        assert repaired.input_schema[Keys.Schema.TYPE] == Values.SchemaType.OBJECT

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

    def test_loader_repairs_typeless_schema_instead_of_dropping_the_server(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        """A tool that omits ``type`` must survive the load, whole connector intact.

        This is the regression the repair exists for. ``parse_tools`` converts
        any ``ValidationError`` into ``MALFORMED_DESCRIPTOR`` for the *entire
        server*, so before the repair this single payload deleted the connector
        from the model's surface -- and because the raise happened inside a
        Pydantic field validator, the user saw no error at all, just an agent
        claiming it could not do the thing.
        """

        client = self.FakeMcpClient(
            tools=(self.typeless_tool_payload(),),
            resources=(),
        )
        loader = self.make_loader(client)

        result = asyncio.run(self.load_default(loader, runtime_context_admin))

        # Registers: the connector survives and the tool reaches the surface.
        assert result.error is None
        assert result.loaded_server is not None
        (tool,) = result.loaded_server.tools
        assert tool.name == self.TestValues.Names.DRIVE_SEARCH

        # ...with every vendor defect repaired rather than dropped.
        schema = tool.input_schema
        assert schema[Keys.Schema.TYPE] == Values.SchemaType.OBJECT
        assert "$defs" in schema and "definitions" not in schema
        assert schema[Keys.Schema.PROPERTIES]["fields"]["$ref"] == "#/$defs/IssueFields"
        assert schema[Keys.Schema.PROPERTIES]["assignee"][Keys.Schema.TYPE] == [
            Values.SchemaType.STRING,
            "null",
        ]
        assert schema[Keys.Schema.REQUIRED] == ["project", "summary"]

        # Dispatches: the repaired tool is invocable through the same client
        # the registry resolves for a real call.
        dispatched = asyncio.run(
            client.call_tool(
                tool_name=tool.name,
                arguments={"project": "ENG", "summary": "Broken schema"},
            )
        )

        assert "ENG" in dispatched["content"][0]["text"]

    def test_loader_attributes_a_repair_to_the_connector_that_shipped_it(
        self,
        runtime_context_admin: AgentRuntimeContext,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The repair line must name the server, driven through the real loader.

        The repair itself runs inside a Pydantic field validator, which cannot
        see the card that hosts the descriptor. ``McpLoader._load_uncached``
        binds the connector identity around the whole discovery span; if that
        binding is ever dropped, the repair still works and every other test
        here still passes, but the line degrades to ``server=-`` and the
        evidence stops naming a vendor to file against.
        """

        client = self.FakeMcpClient(
            tools=(self.typeless_tool_payload(),),
            resources=(),
        )
        loader = self.make_loader(client)

        with caplog.at_level(logging.WARNING):
            result = asyncio.run(self.load_default(loader, runtime_context_admin))

        assert result.error is None
        (record,) = [
            r for r in caplog.records if "mcp_schema_repair.applied" in r.getMessage()
        ]
        message = record.getMessage()
        assert f"server={self.TestValues.Names.DRIVE_MCP}" in message
        assert f"tool={self.TestValues.Names.DRIVE_SEARCH}" in message

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
