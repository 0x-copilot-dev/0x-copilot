"""Unit tests for the P2-3 per-tool MCP ``ToolSource`` (the keystone).

The source is additive and unwired, so these tests are the whole proof that the
per-tool registration path is safe before P2-8 flips it on. They pin four
properties, all of them security- or provenance-bearing:

* **One derivation.** Every ``CapabilityDescriptor`` comes from the P1b
  :class:`McpCapabilityDescriptorSource`; the source never builds one itself and
  never decides an ``action`` / ``trust`` / ``connector_state`` of its own. A
  second derivation would be a second policy, silently diverging from the one
  the PDP was tested against.
* **The annotations bridge is faithful and fail-closed.** ``readOnlyHint`` is a
  tri-state, untrusted hint: ``True`` → READ, ``False`` → WRITE, absent → WRITE,
  non-bool → WRITE, and the curated catalog still outranks all of it.
* **Exposure is gated.** An unauthorized card contributes no tools *and is never
  contacted* — no endpoint lookup, no credential fetch.
* **Failures are typed, safe, and isolated.** One bad connector yields one
  ``McpLoadError`` with a safe public message and never blanks the others; a 4xx
  refusal is never advertised as retryable; an unexpected exception never leaks
  its internal detail.

Everything runs against fakes — no network, no credential, no live MCP server.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import inspect
from typing import Any

import httpx
import pytest
from langchain_core.tools import BaseTool

from agent_runtime.capabilities.mcp import tool_source as tool_source_module
from agent_runtime.capabilities.mcp.annotations import McpToolAnnotationsRegistry
from agent_runtime.capabilities.mcp.cards import (
    McpAuthState,
    McpLoadErrorCode,
    McpServerCard,
    McpTransport,
)
from agent_runtime.capabilities.mcp.client import (
    McpAuthError,
    McpConnectionError,
    McpRequestRejectedError,
)
from agent_runtime.capabilities.mcp.connection import McpServerConnectionConfig
from agent_runtime.capabilities.mcp.constants import Messages
from agent_runtime.capabilities.mcp.descriptor_source import (
    McpCapabilityDescriptorSource,
)
from agent_runtime.capabilities.mcp.tool_naming import McpToolName
from agent_runtime.capabilities.mcp.tool_source import (
    AuthorizedCardLister,
    McpToolCatalog,
    McpToolSource,
)
from agent_runtime.capabilities.policy.contracts import (
    Action,
    CapabilityDescriptor,
    ConnectorState,
    Trust,
)
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    ConnectorAccessMode,
    ModelConfig,
)
from agent_runtime.execution.filesystem_bypass import (
    FilesystemBypassDecision,
    FilesystemBypassMode,
)


class StubMcpTool(BaseTool):
    """A discovered MCP tool, at the shape ``langchain-mcp-adapters`` returns it.

    The adapter flattens the MCP ``ToolAnnotations`` model straight onto
    ``metadata``, so the fake carries the raw camelCase wire keys — the exact
    input ``_ingest_annotations`` has to read.
    """

    name: str = "stub_tool"
    description: str = "A stubbed MCP tool."

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Stub MCP tools are never executed in unit tests.")


class DispatchingStubMcpTool(StubMcpTool):
    """A stub that actually dispatches, modelling the adapter's closure.

    ``convert_mcp_tool_to_langchain_tool`` captures the session and the server's
    own wire name when it builds the tool, so what a call reaches is fixed at
    construction and is NOT read back off ``BaseTool.name``. ``wire`` stands in
    for that captured pair: renaming the tool for the model surface must leave
    it untouched, which is the property that makes namespacing safe at all.
    """

    #: ``"{server}:{wire tool name}"`` — what the connector would receive.
    wire: str = ""

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        return self.wire


class StubAuth(httpx.Auth):
    """A stand-in for P2-2's ``RefreshingBearerAuth`` (never actually invoked)."""


class FakeCardSource:
    """The unfiltered card registry seam :class:`AuthorizedCardLister` reads."""

    def __init__(self, cards: Sequence[McpServerCard]) -> None:
        self._cards = tuple(cards)

    async def list_server_cards(self) -> Sequence[McpServerCard]:
        return self._cards


class FakeConnectionDirectory:
    """Credential-plane endpoint lookup; records every server it was asked for."""

    def __init__(
        self,
        configs: Mapping[str, McpServerConnectionConfig],
        *,
        error: BaseException | None = None,
    ) -> None:
        self._configs = dict(configs)
        self._error = error
        self.requested: list[str] = []

    async def connection_for(self, server_id: str) -> McpServerConnectionConfig:
        self.requested.append(server_id)
        if self._error is not None:
            raise self._error
        return self._configs[server_id]


class FakeCredentialProvider:
    """P0 ``CredentialProvider`` fake; records every server it was asked for."""

    def __init__(
        self,
        auth: httpx.Auth | Mapping[str, str] | object | None = None,
        *,
        error: BaseException | None = None,
    ) -> None:
        self._auth = {} if auth is None else auth
        self._error = error
        self.requested: list[str] = []

    async def auth_for(self, server_id: str) -> Any:
        self.requested.append(server_id)
        if self._error is not None:
            raise self._error
        return self._auth


class FakeMultiServerClient:
    """A fake ``MultiServerMCPClient``: canned tools (or a raise) per server."""

    def __init__(
        self,
        tools_by_server: Mapping[str, Sequence[BaseTool]],
        errors_by_server: Mapping[str, BaseException] | None = None,
    ) -> None:
        self._tools = {name: list(tools) for name, tools in tools_by_server.items()}
        self._errors = dict(errors_by_server or {})
        self.asked: list[str | None] = []

    async def get_tools(self, *, server_name: str | None = None) -> list[BaseTool]:
        self.asked.append(server_name)
        if server_name is not None and server_name in self._errors:
            raise self._errors[server_name]
        return list(self._tools.get(server_name or "", []))


class FakeClientFactory:
    """Captures the built connection map instead of opening anything."""

    def __init__(self, client: FakeMultiServerClient) -> None:
        self._client = client
        self.connections: dict[str, Any] = {}
        self.created = 0

    def create(self, connections: Mapping[str, Any]) -> FakeMultiServerClient:
        self.connections = dict(connections)
        self.created += 1
        return self._client


@dataclass(frozen=True)
class Harness:
    """One wired-up source plus the fakes a test needs to assert against."""

    source: McpToolSource
    factory: FakeClientFactory
    client: FakeMultiServerClient
    directory: FakeConnectionDirectory
    credentials: FakeCredentialProvider


class McpToolSourceFixtureMixin:
    """Cards, contexts, tools, and the assembled source under test."""

    SERVER = "linear"
    SERVER_ID = "srv_linear"
    URL = "https://mcp.linear.app/mcp"

    def card(
        self,
        *,
        name: str | None = None,
        server_id: str | None = SERVER_ID,
        auth_state: McpAuthState = McpAuthState.AUTHENTICATED,
        required_scopes: frozenset[str] = frozenset(),
        enabled: bool = True,
        health: str = "healthy",
        access_mode: ConnectorAccessMode = ConnectorAccessMode.READ,
    ) -> McpServerCard:
        return McpServerCard(
            name=name or self.SERVER,
            server_id=server_id,
            short_description="Linear MCP connector.",
            transport="http",
            auth_mode="oauth2",
            auth_state=auth_state,
            required_scopes=required_scopes,
            health=health,
            load_cost=10,
            enabled=enabled,
            access_mode=access_mode,
        )

    def context(
        self,
        *,
        permission_scopes: frozenset[str] = frozenset(),
        paused_connectors: frozenset[str] = frozenset(),
        connector_access_modes: dict[str, ConnectorAccessMode] | None = None,
        bypass: bool = False,
    ) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id="user_p2",
            org_id="org_p2",
            roles={"employee"},
            permission_scopes=permission_scopes,
            connector_scopes={},
            paused_connectors=paused_connectors,
            connector_access_modes=connector_access_modes or {},
            filesystem_bypass=FilesystemBypassDecision(
                master_enabled=True,
                mode=(
                    FilesystemBypassMode.BYPASS
                    if bypass
                    else FilesystemBypassMode.MANUAL
                ),
            ),
            user_policies_json={},
            model_profile=ModelConfig(
                provider="openai",
                model_name="gpt-4o-mini",
                max_input_tokens=4096,
                timeout_seconds=30,
                temperature=0.0,
            ),
            run_id="run_p2",
            trace_id="trace_p2",
        )

    def tool(self, name: str, metadata: dict[str, Any] | None = None) -> StubMcpTool:
        return StubMcpTool(name=name, metadata=metadata)

    def dispatching_tool(self, name: str, *, server: str) -> DispatchingStubMcpTool:
        """A tool whose call target is captured at construction, as the adapter does."""

        return DispatchingStubMcpTool(name=name, wire=f"{server}:{name}")

    def config(
        self,
        *,
        transport: McpTransport = McpTransport.HTTP,
        headers: dict[str, str] | None = None,
    ) -> McpServerConnectionConfig:
        return McpServerConnectionConfig(
            url=self.URL,
            transport=transport,
            headers=headers or {},
        )

    def harness(
        self,
        *,
        cards: Sequence[McpServerCard] | None = None,
        tools_by_server: Mapping[str, Sequence[BaseTool]] | None = None,
        errors_by_server: Mapping[str, BaseException] | None = None,
        configs: Mapping[str, McpServerConnectionConfig] | None = None,
        directory_error: BaseException | None = None,
        auth: httpx.Auth | Mapping[str, str] | object | None = None,
        credential_error: BaseException | None = None,
        context: AgentRuntimeContext | None = None,
        reserved_names: frozenset[str] = frozenset(),
    ) -> Harness:
        client = FakeMultiServerClient(
            tools_by_server if tools_by_server is not None else {},
            errors_by_server,
        )
        factory = FakeClientFactory(client)
        directory = FakeConnectionDirectory(
            configs if configs is not None else {self.SERVER_ID: self.config()},
            error=directory_error,
        )
        credentials = FakeCredentialProvider(auth, error=credential_error)
        source = McpToolSource(
            context=context if context is not None else self.context(),
            cards=AuthorizedCardLister(
                cards=FakeCardSource(cards if cards is not None else [self.card()])
            ),
            directory=directory,
            credentials=credentials,
            client_factory=factory,
            reserved_names=reserved_names,
        )
        return Harness(
            source=source,
            factory=factory,
            client=client,
            directory=directory,
            credentials=credentials,
        )

    def registered(self, tool_name: str, server: str | None = None) -> str:
        """The model-surface name ``tool_name`` registers under on ``server``."""

        return McpToolName.compose(server=server or self.SERVER, tool=tool_name)

    def action_for(self, catalog: McpToolCatalog, tool_name: str) -> Action:
        """The action derived for a tool, addressed by its BARE name.

        Tests name the tool the way the connector advertises it; registration
        namespaces it. Composing here keeps every caller written in the register
        a test author is actually thinking in.
        """

        registered = self.registered(tool_name)
        return next(
            descriptor.action
            for tool, descriptor in catalog.pairs
            if tool.name == registered
        )


class BoundAnnotationsRegistryMixin:
    """Binds the per-run annotations registry the ingest writes into.

    In production the run binds it (the worker), not the source: the registry
    has to outlive ``load()`` because P2-4 re-derives the descriptor per call.
    """

    @pytest.fixture(autouse=True)
    def _bound_annotations_registry(self):
        token = McpToolAnnotationsRegistry.bind_for_run({})
        try:
            yield
        finally:
            McpToolAnnotationsRegistry.unbind(token)


class TestLoadProducesPairs(McpToolSourceFixtureMixin, BoundAnnotationsRegistryMixin):
    async def test_returns_one_pair_per_discovered_tool(self) -> None:
        harness = self.harness(
            tools_by_server={
                self.SERVER: [self.tool("list_issues"), self.tool("create_issue")]
            }
        )
        pairs = await harness.source.load()
        assert [tool.name for tool, _ in pairs] == [
            "mcp__linear__list_issues",
            "mcp__linear__create_issue",
        ]
        assert all(isinstance(d, CapabilityDescriptor) for _, d in pairs)

    async def test_descriptor_identity_comes_from_the_p1b_derivation(self) -> None:
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("list_issues")]}
        )
        ((_, descriptor),) = await harness.source.load()
        assert descriptor.urn == "mcp:linear:list_issues"
        assert descriptor.source == "mcp"
        assert descriptor.action is Action.READ
        assert descriptor.trust is Trust.TRUSTED
        assert descriptor.connector_state is ConnectorState.LIVE

    async def test_no_authorized_card_loads_nothing_and_builds_no_client(self) -> None:
        harness = self.harness(cards=[])
        assert await harness.source.load() == []
        assert harness.factory.created == 0
        assert harness.source.catalog.failures == ()

    async def test_catalog_before_load_is_empty(self) -> None:
        harness = self.harness()
        assert harness.source.catalog.pairs == ()
        assert dict(harness.source.catalog.tool_to_server) == {}


class TestAnnotationsTriState(McpToolSourceFixtureMixin, BoundAnnotationsRegistryMixin):
    """``readOnlyHint`` is untrusted and tighten-only — silence is never read."""

    async def _action(self, metadata: dict[str, Any] | None) -> Action:
        # ``get_issues`` is deliberately absent from the curated catalog, so the
        # annotation rung is the one under test.
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("get_issues", metadata)]}
        )
        await harness.source.load()
        return self.action_for(harness.source.catalog, "get_issues")

    async def test_read_only_hint_true_yields_read(self) -> None:
        assert await self._action({"readOnlyHint": True}) is Action.READ

    async def test_absent_annotations_fail_closed_to_write(self) -> None:
        assert await self._action(None) is Action.WRITE

    async def test_read_only_hint_false_fails_closed_to_write(self) -> None:
        assert await self._action({"readOnlyHint": False}) is Action.WRITE

    async def test_read_only_hint_null_fails_closed_to_write(self) -> None:
        assert await self._action({"readOnlyHint": None}) is Action.WRITE

    async def test_non_bool_hint_is_no_hint(self) -> None:
        # A truthy non-bool must never be trusted into a read.
        assert await self._action({"readOnlyHint": "true"}) is Action.WRITE

    async def test_destructive_hint_tightens_to_destructive(self) -> None:
        assert await self._action({"destructiveHint": True}) is Action.DESTRUCTIVE

    async def test_unmodelled_wire_keys_are_ignored(self) -> None:
        # ``title`` / ``openWorldHint`` / vendor extras ride the same flat
        # metadata dict and must not break the strict ingest.
        assert (
            await self._action(
                {
                    "title": "Get issues",
                    "openWorldHint": True,
                    "vendor.private": {"x": 1},
                    "readOnlyHint": True,
                }
            )
            is Action.READ
        )

    async def test_catalog_outranks_a_lying_annotation(self) -> None:
        harness = self.harness(
            tools_by_server={
                self.SERVER: [
                    self.tool("create_issue", {"readOnlyHint": True}),
                    self.tool("list_issues", {"destructiveHint": True}),
                ]
            }
        )
        await harness.source.load()
        catalog = harness.source.catalog
        assert self.action_for(catalog, "create_issue") is Action.WRITE
        assert self.action_for(catalog, "list_issues") is Action.READ

    async def test_hints_land_in_the_run_registry_before_derivation(self) -> None:
        harness = self.harness(
            tools_by_server={
                self.SERVER: [self.tool("get_issues", {"readOnlyHint": True})]
            }
        )
        await harness.source.load()
        captured = McpToolAnnotationsRegistry.get(self.SERVER, "get_issues")
        assert captured is not None
        assert captured.read_only_hint is True
        # Derivation already saw it — the ordering is what makes READ possible.
        assert self.action_for(harness.source.catalog, "get_issues") is Action.READ


class TestAnnotationsRequireABoundRunRegistry(McpToolSourceFixtureMixin):
    """Without a bound run registry the ingest is inert — and that fails closed."""

    async def test_unbound_registry_falls_back_to_write(self) -> None:
        assert McpToolAnnotationsRegistry.active() is None
        harness = self.harness(
            tools_by_server={
                self.SERVER: [self.tool("get_issues", {"readOnlyHint": True})]
            }
        )
        await harness.source.load()
        assert self.action_for(harness.source.catalog, "get_issues") is Action.WRITE


class TestAuthorizationGate(McpToolSourceFixtureMixin, BoundAnnotationsRegistryMixin):
    async def test_card_missing_a_required_scope_is_excluded(self) -> None:
        harness = self.harness(
            cards=[self.card(required_scopes=frozenset({"linear:write"}))],
            tools_by_server={self.SERVER: [self.tool("list_issues")]},
        )
        assert await harness.source.load() == []

    async def test_unauthorized_card_is_never_contacted(self) -> None:
        # The security property, not just the exclusion: an unauthorized
        # connector must not have its endpoint resolved or a credential minted.
        harness = self.harness(
            cards=[self.card(required_scopes=frozenset({"linear:write"}))],
            tools_by_server={self.SERVER: [self.tool("list_issues")]},
        )
        await harness.source.load()
        assert harness.directory.requested == []
        assert harness.credentials.requested == []
        assert harness.factory.created == 0

    async def test_paused_connector_is_excluded(self) -> None:
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("list_issues")]},
            context=self.context(paused_connectors=frozenset({self.SERVER_ID})),
        )
        assert await harness.source.load() == []

    async def test_access_mode_off_connector_is_excluded(self) -> None:
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("list_issues")]},
            context=self.context(
                connector_access_modes={self.SERVER_ID: ConnectorAccessMode.OFF}
            ),
        )
        assert await harness.source.load() == []

    @pytest.mark.parametrize(
        ("enabled", "health"), [(False, "healthy"), (True, "unavailable")]
    )
    async def test_disabled_or_unhealthy_card_is_excluded(
        self, enabled: bool, health: str
    ) -> None:
        harness = self.harness(
            cards=[self.card(enabled=enabled, health=health)],
            tools_by_server={self.SERVER: [self.tool("list_issues")]},
        )
        assert await harness.source.load() == []
        assert harness.directory.requested == []


class TestPublishedMaps(McpToolSourceFixtureMixin, BoundAnnotationsRegistryMixin):
    async def test_cards_by_urn_covers_every_registered_tool(self) -> None:
        card = self.card()
        harness = self.harness(
            cards=[card],
            tools_by_server={
                self.SERVER: [self.tool("list_issues"), self.tool("create_issue")]
            },
        )
        await harness.source.load()
        cards_by_urn = harness.source.catalog.cards_by_urn
        assert set(cards_by_urn) == {
            "mcp:linear:list_issues",
            "mcp:linear:create_issue",
        }
        assert all(value == card for value in cards_by_urn.values())

    async def test_tool_to_server_feeds_the_connector_resolver(self) -> None:
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("create_issue")]}
        )
        await harness.source.load()
        catalog = harness.source.catalog
        # Keyed on the MODEL-surface name, because that is what ``tool_name``
        # carries on the stream events the resolver is queried from.
        assert dict(catalog.tool_to_server) == {"mcp__linear__create_issue": "linear"}
        resolver = catalog.connector_resolver()
        assert resolver.server_for("mcp__linear__create_issue") == "linear"
        assert resolver.server_for("create_issue") is None
        assert resolver.server_for("write_todos") is None

    async def test_published_maps_are_read_only_snapshots(self) -> None:
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("create_issue")]}
        )
        await harness.source.load()
        catalog = harness.source.catalog
        with pytest.raises(TypeError):
            catalog.tool_to_server["injected"] = "attacker"  # type: ignore[index]
        with pytest.raises(TypeError):
            catalog.cards_by_urn["mcp:evil:tool"] = self.card()  # type: ignore[index]


class TestConnectionBuilding(McpToolSourceFixtureMixin, BoundAnnotationsRegistryMixin):
    async def test_http_config_builds_a_streamable_http_connection(self) -> None:
        auth = StubAuth()
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("list_issues")]},
            configs={self.SERVER_ID: self.config(headers={"X-Trace": "abc"})},
            auth=auth,
        )
        await harness.source.load()
        connection = harness.factory.connections["linear"]
        assert connection["transport"] == "streamable_http"
        assert connection["url"] == self.URL
        assert connection["headers"] == {"X-Trace": "abc"}
        # The rotating credential rides the auth slot, never a frozen header.
        assert connection["auth"] is auth
        assert harness.credentials.requested == [self.SERVER_ID]

    async def test_mapping_credentials_ride_headers_not_the_auth_slot(self) -> None:
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("list_issues")]},
            auth={"Authorization": "Bearer token-value"},
        )
        await harness.source.load()
        connection = harness.factory.connections["linear"]
        assert connection["headers"] == {"Authorization": "Bearer token-value"}
        assert "auth" not in connection

    async def test_sse_config_builds_an_sse_connection(self) -> None:
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("list_issues")]},
            configs={self.SERVER_ID: self.config(transport=McpTransport.SSE)},
        )
        await harness.source.load()
        assert harness.factory.connections["linear"]["transport"] == "sse"

    async def test_stdio_is_refused_as_unsupported_transport(self) -> None:
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("list_issues")]},
            configs={self.SERVER_ID: self.config(transport=McpTransport.STDIO)},
        )
        assert await harness.source.load() == []
        (failure,) = harness.source.catalog.failures
        assert failure.code is McpLoadErrorCode.UNSUPPORTED_TRANSPORT
        assert failure.safe_message == Messages.Loader.UNSUPPORTED_TRANSPORT
        assert failure.retryable is False
        assert harness.factory.created == 0

    async def test_card_without_a_server_id_is_refused(self) -> None:
        harness = self.harness(
            cards=[self.card(server_id=None)],
            tools_by_server={self.SERVER: [self.tool("list_issues")]},
        )
        assert await harness.source.load() == []
        (failure,) = harness.source.catalog.failures
        assert failure.code is McpLoadErrorCode.UNKNOWN_SERVER
        assert failure.safe_message == Messages.Registry.REQUESTED_SERVER_UNKNOWN
        assert harness.directory.requested == []

    async def test_unrecognised_credential_shape_is_refused(self) -> None:
        # A provider defect must never degrade into an unauthenticated connect.
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("list_issues")]},
            auth=object(),
        )
        assert await harness.source.load() == []
        (failure,) = harness.source.catalog.failures
        assert failure.code is McpLoadErrorCode.AUTH_FAILURE
        assert failure.safe_message == Messages.Loader.AUTH_FAILED
        assert failure.retryable is False


class TestFailureIsolation(McpToolSourceFixtureMixin, BoundAnnotationsRegistryMixin):
    def _two_cards(self) -> list[McpServerCard]:
        return [
            self.card(name="github", server_id="srv_github"),
            self.card(name="linear", server_id=self.SERVER_ID),
        ]

    def _two_configs(self) -> dict[str, McpServerConnectionConfig]:
        return {"srv_github": self.config(), self.SERVER_ID: self.config()}

    async def test_one_unreachable_server_does_not_blank_the_others(self) -> None:
        harness = self.harness(
            cards=self._two_cards(),
            configs=self._two_configs(),
            tools_by_server={"linear": [self.tool("list_issues")]},
            errors_by_server={"github": McpConnectionError("socket died")},
        )
        pairs = await harness.source.load()
        assert [tool.name for tool, _ in pairs] == ["mcp__linear__list_issues"]
        (failure,) = harness.source.catalog.failures
        assert failure.code is McpLoadErrorCode.CONNECTION_FAILED
        assert failure.safe_message == Messages.Loader.CONNECTION_FAILED
        assert failure.retryable is True
        assert failure.server_name == "github"

    async def test_auth_failure_is_typed_and_not_retryable(self) -> None:
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("list_issues")]},
            errors_by_server={self.SERVER: McpAuthError("token expired")},
        )
        assert await harness.source.load() == []
        (failure,) = harness.source.catalog.failures
        assert failure.code is McpLoadErrorCode.AUTH_FAILURE
        assert failure.safe_message == Messages.Loader.AUTH_FAILED
        assert failure.retryable is False

    async def test_a_4xx_refusal_is_never_advertised_as_retryable(self) -> None:
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("list_issues")]},
            errors_by_server={
                self.SERVER: McpRequestRejectedError("bad request", status_code=400)
            },
        )
        assert await harness.source.load() == []
        (failure,) = harness.source.catalog.failures
        assert failure.code is McpLoadErrorCode.MCP_PROTOCOL_ERROR
        assert failure.safe_message == Messages.Loader.REQUEST_REJECTED
        assert failure.retryable is False

    async def test_directory_failure_is_typed_and_isolated(self) -> None:
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("list_issues")]},
            directory_error=McpConnectionError("broker unreachable"),
        )
        assert await harness.source.load() == []
        (failure,) = harness.source.catalog.failures
        assert failure.code is McpLoadErrorCode.CONNECTION_FAILED
        assert failure.retryable is True

    async def test_credential_failure_is_typed_and_isolated(self) -> None:
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("list_issues")]},
            credential_error=McpAuthError("keychain refused"),
        )
        assert await harness.source.load() == []
        (failure,) = harness.source.catalog.failures
        assert failure.code is McpLoadErrorCode.AUTH_FAILURE
        assert failure.safe_message == Messages.Loader.AUTH_FAILED

    async def test_unexpected_error_never_leaks_internal_detail(self) -> None:
        secret = "/Users/someone/.secrets/token.json"
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("list_issues")]},
            errors_by_server={
                self.SERVER: RuntimeError(f"boom while reading {secret}")
            },
        )
        assert await harness.source.load() == []
        (failure,) = harness.source.catalog.failures
        assert failure.code is McpLoadErrorCode.CONNECTION_FAILED
        assert failure.safe_message == Messages.Loader.LOAD_FAILED
        assert secret not in failure.safe_message
        assert "boom" not in failure.safe_message

    async def test_two_connectors_may_both_expose_the_same_tool_name(self) -> None:
        # The reason the namespace exists. Before it, the second ``search`` lost
        # a DUPLICATE_DESCRIPTOR_NAME race and its connector was unusable for the
        # whole run; the registered name now carries the connector, so both are
        # callable and neither is dropped.
        harness = self.harness(
            cards=self._two_cards(),
            configs=self._two_configs(),
            tools_by_server={
                "github": [self.tool("search")],
                "linear": [self.tool("search"), self.tool("list_issues")],
            },
        )

        pairs = await harness.source.load()

        assert [tool.name for tool, _ in pairs] == [
            "mcp__github__search",
            "mcp__linear__search",
            "mcp__linear__list_issues",
        ]
        assert harness.source.catalog.failures == ()

    async def test_each_shared_name_still_resolves_to_its_own_connector(self) -> None:
        # The provenance half: an ambiguous ``tool_name -> server_slug`` map
        # would stamp the wrong connector onto a call's events and police it
        # against the wrong card. Two entries, two servers, no ambiguity.
        harness = self.harness(
            cards=self._two_cards(),
            configs=self._two_configs(),
            tools_by_server={
                "github": [self.tool("search")],
                "linear": [self.tool("search")],
            },
        )

        await harness.source.load()

        resolver = harness.source.catalog.connector_resolver()
        assert resolver.server_for("mcp__github__search") == "github"
        assert resolver.server_for("mcp__linear__search") == "linear"
        # The bare name addresses nothing: it is not on the model surface, so it
        # can never arrive on an event.
        assert resolver.server_for("search") is None

    async def test_each_shared_name_dispatches_to_its_own_server(self) -> None:
        # The registered name is renamed; the DISPATCH is not. ``langchain-mcp-
        # adapters`` captures the wire name and the session in the tool's own
        # closure, so the two ``search`` tools must still call two servers.
        harness = self.harness(
            cards=self._two_cards(),
            configs=self._two_configs(),
            tools_by_server={
                "github": [self.dispatching_tool("search", server="github")],
                "linear": [self.dispatching_tool("search", server="linear")],
            },
        )

        pairs = await harness.source.load()

        dispatched = {
            tool.name: await tool.ainvoke({"query": "issues"}) for tool, _ in pairs
        }
        assert dispatched == {
            "mcp__github__search": "github:search",
            "mcp__linear__search": "linear:search",
        }

    async def test_a_genuine_clash_of_namespaced_names_is_still_typed(self) -> None:
        # The collision check is not removed, only made precise: two tools whose
        # names collapse to one under the provider charset still cannot share a
        # registration, and the loser is visible rather than silent.
        harness = self.harness(
            tools_by_server={
                self.SERVER: [self.tool("list_issues"), self.tool("list__issues")]
            }
        )

        pairs = await harness.source.load()

        assert [tool.name for tool, _ in pairs] == ["mcp__linear__list_issues"]
        (failure,) = harness.source.catalog.failures
        assert failure.code is McpLoadErrorCode.DUPLICATE_DESCRIPTOR_NAME
        assert failure.safe_message == Messages.Loader.DUPLICATE_TOOL_NAMES
        assert failure.server_name == "linear"

    async def test_blank_tool_name_is_dropped(self) -> None:
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("   "), self.tool("list_issues")]}
        )
        pairs = await harness.source.load()
        assert [tool.name for tool, _ in pairs] == ["mcp__linear__list_issues"]


class TestSingleDerivationPath(
    McpToolSourceFixtureMixin, BoundAnnotationsRegistryMixin
):
    """The P1b source is the ONLY place a descriptor is derived."""

    async def test_every_descriptor_comes_from_the_p1b_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[str, str, str]] = []
        sentinel = CapabilityDescriptor(
            urn="mcp:linear:sentinel",
            action=Action.READ,
            trust=Trust.TRUSTED,
            source="mcp",
            connector_state=ConnectorState.LIVE,
        )

        def _spy(
            *,
            card: McpServerCard,
            server: str,
            tool: str,
            context: AgentRuntimeContext,
        ) -> CapabilityDescriptor:
            calls.append((card.name, server, tool))
            return sentinel

        monkeypatch.setattr(McpCapabilityDescriptorSource, "describe", _spy)
        harness = self.harness(
            tools_by_server={
                self.SERVER: [self.tool("list_issues"), self.tool("create_issue")]
            }
        )
        pairs = await harness.source.load()
        # Every emitted descriptor IS the one the P1b source returned — the
        # source adds nothing and overrides nothing.
        assert [descriptor for _, descriptor in pairs] == [sentinel, sentinel]
        assert calls == [
            ("linear", "linear", "list_issues"),
            ("linear", "linear", "create_issue"),
        ]

    def test_module_never_derives_a_descriptor_itself(self) -> None:
        # Structural sibling of the behavioural test above: the module may not
        # construct a descriptor or reach for the policy vocabulary at all, so a
        # second derivation cannot be added without this failing.
        source_text = inspect.getsource(tool_source_module)
        assert "CapabilityDescriptor(" not in source_text
        for forbidden in ("Action.", "Trust.", "ConnectorState.", "Posture."):
            assert forbidden not in source_text


class TestServerSlugCollision(McpToolSourceFixtureMixin, BoundAnnotationsRegistryMixin):
    """``server_slug`` is lossy, so two legal card names can land on one key."""

    #: The winner is deterministic, not incidental: ``AuthorizedCardLister``
    #: sorts by name, and ``-`` (0x2D) precedes ``_`` (0x5F), so ``linear-app``
    #: is always processed first and ``linear_app`` is always the dropped one.
    WINNER_ID = "srv_two"
    DROPPED_NAME = "linear_app"

    def _colliding_cards(self) -> list[McpServerCard]:
        # Both names satisfy McpServerCard's ^[a-z0-9][a-z0-9_-]*$ and are
        # distinct, yet server_slug collapses '_' and '-' alike -> "linear-app".
        return [
            self.card(name="linear_app", server_id="srv_one"),
            self.card(name="linear-app", server_id=self.WINNER_ID),
        ]

    async def test_collision_is_reported_not_silently_dropped(self) -> None:
        harness = self.harness(
            cards=self._colliding_cards(),
            configs={
                "srv_one": self.config(),
                "srv_two": self.config(),
            },
            tools_by_server={"linear-app": [self.tool("list_issues")]},
        )

        await harness.source.load()

        # The second card loses, but it is VISIBLE: a typed failure names it, so
        # the surface can tell the user a connector did not register.
        (failure,) = harness.source.catalog.failures
        assert failure.code is McpLoadErrorCode.DUPLICATE_SERVER_NAME
        assert failure.safe_message == Messages.Registry.DUPLICATE_SERVER_NAME
        assert failure.server_name == self.DROPPED_NAME

    async def test_no_credential_is_minted_for_the_dropped_card(self) -> None:
        harness = self.harness(
            cards=self._colliding_cards(),
            configs={"srv_one": self.config(), "srv_two": self.config()},
            tools_by_server={"linear-app": [self.tool("list_issues")]},
        )

        await harness.source.load()

        # The collision is detected BEFORE the credential plane is touched, so a
        # token is never minted for a card that cannot register.
        assert harness.credentials.requested == [self.WINNER_ID]
        assert harness.directory.requested == [self.WINNER_ID]

    async def test_the_first_card_still_registers_its_tools(self) -> None:
        harness = self.harness(
            cards=self._colliding_cards(),
            configs={"srv_one": self.config(), "srv_two": self.config()},
            tools_by_server={"linear-app": [self.tool("list_issues")]},
        )

        pairs = await harness.source.load()

        assert [tool.name for tool, _ in pairs] == ["mcp__linear-app__list_issues"]


class TestNativeToolNameCollision(
    McpToolSourceFixtureMixin, BoundAnnotationsRegistryMixin
):
    """A connector may not shadow a native tool's name (LOCAL_TOOL_COLLISION)."""

    async def test_a_builtins_name_is_no_longer_reachable_to_shadow(self) -> None:
        # The namespace turns the whole class of shadowing from "dropped with a
        # typed failure" into "cannot arise". A connector advertising
        # ``write_todos`` registers as ``mcp__linear__write_todos``, shadows the
        # TodoListMiddleware builtin not at all, and the user keeps the tool
        # instead of losing it to a name they never chose.
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("write_todos")]},
            reserved_names=frozenset({"write_todos"}),
        )

        pairs = await harness.source.load()

        assert [tool.name for tool, _ in pairs] == ["mcp__linear__write_todos"]
        assert harness.source.catalog.failures == ()

    async def test_reserved_name_is_refused_with_a_typed_failure(self) -> None:
        # What survives the namespace: a NATIVE tool that itself carries the
        # ``mcp__`` shape is the one case the prefix cannot separate, so the
        # guard stays and still fails typed rather than letting a connector
        # publish ``tool_to_server`` for a builtin's own name.
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("write_todos")]},
            reserved_names=frozenset({"mcp__linear__write_todos"}),
        )

        pairs = await harness.source.load()

        assert pairs == []
        (failure,) = harness.source.catalog.failures
        assert failure.code is McpLoadErrorCode.LOCAL_TOOL_COLLISION
        assert failure.safe_message == Messages.Loader.LOCAL_TOOL_COLLISION

    async def test_reserved_name_never_reaches_the_published_map(self) -> None:
        # The load-bearing half: tool_to_server feeds the stream seam's connector
        # resolver. If a builtin's name landed there, StreamMessageProcessor
        # would stamp MCP provenance + access_mode onto the BUILTIN's own events
        # -- exactly the invariant "a native tool must not acquire MCP identity".
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("write_todos")]},
            reserved_names=frozenset({"mcp__linear__write_todos"}),
        )

        await harness.source.load()

        catalog = harness.source.catalog
        assert "mcp__linear__write_todos" not in catalog.tool_to_server
        assert (
            catalog.connector_resolver().server_for("mcp__linear__write_todos") is None
        )

    async def test_non_reserved_tools_on_the_same_server_still_load(self) -> None:
        harness = self.harness(
            tools_by_server={
                self.SERVER: [self.tool("write_todos"), self.tool("list_issues")]
            },
            reserved_names=frozenset({"mcp__linear__write_todos"}),
        )

        pairs = await harness.source.load()

        # One poisoned name does not cost the connector its whole toolset.
        assert [tool.name for tool, _ in pairs] == ["mcp__linear__list_issues"]
        assert harness.source.catalog.tool_to_server == {
            "mcp__linear__list_issues": self.SERVER
        }

    async def test_no_reserved_names_keeps_every_tool(self) -> None:
        harness = self.harness(
            tools_by_server={self.SERVER: [self.tool("write_todos")]},
        )

        pairs = await harness.source.load()

        # The default is empty: the guard only fires on names the host declares.
        assert [tool.name for tool, _ in pairs] == ["mcp__linear__write_todos"]
        assert harness.source.catalog.failures == ()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
