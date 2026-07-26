"""Unit tests for the desktop-local browser MCP provider (AC8).

These tests drive the provider + client against a fake broker (``httpx``
``MockTransport`` — never a real socket, never a real browser) and assert:

- the ``build_browser_mcp`` seam fails closed (disabled / wrong profile /
  missing broker url or token) and only builds a provider when fully configured;
- the single ``desktop_browser`` card lists, is visible, and coexists with a
  backend-style provider WITHOUT a duplicate-name error;
- only the read-only tool surface is discovered (no side-effecting tools);
- handshake audience is verified; auth failures raise the typed ``McpAuthError``.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.capabilities.browser import (
    BrowserReadinessReason,
    BrowserMcpConfig,
    DesktopBrowserMcpProvider,
    build_browser_mcp,
)
from agent_runtime.capabilities.browser.constants import BrowserBroker
from agent_runtime.capabilities.mcp.cards import McpServerCard, McpServerHealth
from agent_runtime.capabilities.mcp.client import McpAuthError, McpConnectionError
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry


class BrowserProviderFixtures:
    """Shared context + fake-broker builders."""

    class Values:
        BROKER_URL = "http://127.0.0.1:54321"
        TOKEN = "boot-credential"
        DESKTOP = "single_user_desktop"

    def context(self) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id="user-1",
            org_id="org-1",
            run_id="run-1",
            roles=frozenset({"employee"}),
            model_profile=ModelConfig(
                provider="openai",
                model_name="gpt-test",
                max_input_tokens=128_000,
                timeout_seconds=30.0,
                temperature=0.0,
            ),
        )

    def tool_list_payload(self) -> dict[str, object]:
        return {
            "tools": [
                {
                    "name": "browser_navigate",
                    "description": "Navigate to an approved HTTPS origin.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                    },
                },
                {
                    "name": "browser_snapshot",
                    "description": "Capture a bounded accessibility snapshot.",
                    "inputSchema": {"type": "object", "properties": {}},
                },
                {
                    "name": "browser_click",
                    "description": "Propose an exact reviewed browser click.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"sessionRef": {"type": "string"}},
                    },
                },
                {
                    "name": "browser_submit",
                    "description": "Propose an exact reviewed browser submission.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"sessionRef": {"type": "string"}},
                    },
                },
            ]
        }

    def fake_broker(
        self,
        *,
        audience: str = BrowserBroker.AUDIENCE,
        unauthorized: bool = False,
    ) -> httpx.AsyncClient:
        payloads = self

        def handler(request: httpx.Request) -> httpx.Response:
            if unauthorized:
                return httpx.Response(401, json={"error": "unauthorized"})
            if request.url.path == BrowserBroker.ROUTE_HANDSHAKE:
                return httpx.Response(200, json={"protocol": "1", "audience": audience})
            if request.url.path == BrowserBroker.ROUTE_TOOLS_LIST:
                return httpx.Response(200, json=payloads.tool_list_payload())
            if request.url.path == BrowserBroker.ROUTE_ACTION:
                body = json.loads(request.content.decode())
                assert body["tool"]["name"] == "browser_navigate"
                assert body["runId"] == "run-1"
                assert body["workspaceId"] == "org-1"
                assert "action" not in body
                assert "binding" not in body
                assert "actionClass" not in body
                assert "originPolicy" not in body
                return httpx.Response(
                    200,
                    json={
                        "result": {
                            "version": 1,
                            "requestId": body["requestId"],
                            "sessionId": "ses",
                            "actionId": "act",
                            "status": "succeeded",
                            "safeSummary": "navigated (200)",
                            "artifactRefs": [],
                        }
                    },
                )
            return httpx.Response(404, json={"error": "not_found"})

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def build_provider(self, client: httpx.AsyncClient) -> DesktopBrowserMcpProvider:
        return DesktopBrowserMcpProvider(
            broker_url=self.Values.BROKER_URL,
            broker_token=self.Values.TOKEN,
            runtime_context=self.context(),
            http_client=client,
        )


async def test_named_local_identity_and_fixed_audience_reach_browser_broker_headers() -> (
    None
):
    context = BrowserProviderFixtures().context()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == BrowserBroker.ROUTE_HANDSHAKE:
            return httpx.Response(
                200, json={"protocol": "1", "audience": BrowserBroker.AUDIENCE}
            )
        return httpx.Response(200, json={"tools": []})

    provider = build_browser_mcp(
        BrowserMcpConfig(
            enabled=True,
            deployment_profile=BrowserProviderFixtures.Values.DESKTOP,
            broker_url=BrowserProviderFixtures.Values.BROKER_URL,
            broker_token=BrowserProviderFixtures.Values.TOKEN,
            service_identity="ai-backend",
            broker_audience="desktop-browser-broker",
            runtime_context=context,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
    )
    assert provider is not None
    await provider.readiness()
    assert seen[0].headers["x-desktop-local-service"] == "ai-backend"
    assert seen[0].headers["x-desktop-local-audience"] == "desktop-browser-broker"


class TestBuildBrowserMcpSeam(BrowserProviderFixtures):
    def _config(self, **overrides: object) -> BrowserMcpConfig:
        base = dict(
            enabled=True,
            deployment_profile=self.Values.DESKTOP,
            broker_url=self.Values.BROKER_URL,
            broker_token=self.Values.TOKEN,
            runtime_context=self.context(),
        )
        base.update(overrides)
        return BrowserMcpConfig(**base)  # type: ignore[arg-type]

    def test_returns_provider_when_fully_configured(self) -> None:
        provider = build_browser_mcp(self._config(http_client=self.fake_broker()))
        assert isinstance(provider, DesktopBrowserMcpProvider)

    def test_returns_none_when_disabled(self) -> None:
        assert build_browser_mcp(self._config(enabled=False)) is None

    def test_returns_none_outside_desktop_profile(self) -> None:
        assert build_browser_mcp(self._config(deployment_profile="enterprise")) is None

    def test_returns_none_without_broker_url_or_token(self) -> None:
        assert build_browser_mcp(self._config(broker_url=None)) is None
        assert build_browser_mcp(self._config(broker_token=None)) is None


class TestProviderCard(BrowserProviderFixtures):
    def test_lists_single_healthy_desktop_browser_card(self) -> None:
        provider = self.build_provider(self.fake_broker())
        cards = asyncio.run(provider.list_server_cards())
        assert len(cards) == 1
        assert cards[0].name == "desktop_browser"
        assert cards[0].health == McpServerHealth.HEALTHY

    def test_coexists_with_backend_provider_without_duplicate(self) -> None:
        provider = self.build_provider(self.fake_broker())

        class EmptyBackendProvider:
            async def list_server_cards(self) -> tuple[McpServerCard, ...]:
                return ()

            def create_client(self, card: McpServerCard) -> object:
                raise NotImplementedError

        registry = DynamicMcpRegistry(providers=(provider, EmptyBackendProvider()))
        cards = asyncio.run(registry.list_server_cards(self.context()))
        names = [card.name for card in cards]
        assert names == ["desktop_browser"]

    def test_hides_card_when_broker_session_is_unauthorized(self) -> None:
        provider = self.build_provider(self.fake_broker(unauthorized=True))
        readiness = asyncio.run(provider.readiness())
        assert not readiness.available
        assert readiness.reason is BrowserReadinessReason.SESSION_UNAUTHORIZED
        assert asyncio.run(provider.list_server_cards()) == ()

    def test_hides_card_when_broker_has_no_read_permission(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == BrowserBroker.ROUTE_HANDSHAKE:
                return httpx.Response(200, json={"audience": BrowserBroker.AUDIENCE})
            if request.url.path == BrowserBroker.ROUTE_TOOLS_LIST:
                return httpx.Response(200, json={"tools": []})
            return httpx.Response(404, json={})

        provider = self.build_provider(
            httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        readiness = asyncio.run(provider.readiness())
        assert not readiness.available
        assert readiness.reason is BrowserReadinessReason.READ_PERMISSION_UNAVAILABLE
        assert asyncio.run(provider.list_server_cards()) == ()

    def test_card_recovers_after_broker_reconnect(self) -> None:
        online = False

        def handler(request: httpx.Request) -> httpx.Response:
            if not online:
                return httpx.Response(503, json={})
            if request.url.path == BrowserBroker.ROUTE_HANDSHAKE:
                return httpx.Response(200, json={"audience": BrowserBroker.AUDIENCE})
            if request.url.path == BrowserBroker.ROUTE_TOOLS_LIST:
                return httpx.Response(200, json=self.tool_list_payload())
            return httpx.Response(404, json={})

        provider = self.build_provider(
            httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        assert asyncio.run(provider.list_server_cards()) == ()
        online = True
        assert [card.name for card in asyncio.run(provider.list_server_cards())] == [
            "desktop_browser"
        ]


class TestClientTransport(BrowserProviderFixtures):
    def test_connect_verifies_audience(self) -> None:
        provider = self.build_provider(self.fake_broker())
        client = provider.create_client((asyncio.run(provider.list_server_cards()))[0])
        meta = asyncio.run(client.connect())
        assert meta.server_name == "desktop_browser"

    def test_connect_rejects_wrong_audience(self) -> None:
        provider = self.build_provider(self.fake_broker(audience="evil"))
        client = provider.create_client(provider._card())
        with pytest.raises(McpConnectionError):
            asyncio.run(client.connect())

    def test_list_tools_returns_only_read_only_surface(self) -> None:
        provider = self.build_provider(self.fake_broker())
        card = asyncio.run(provider.list_server_cards())[0]
        tools = asyncio.run(provider.create_client(card).list_tools())
        names = {tool.name for tool in tools}
        assert names == {"browser_navigate", "browser_snapshot"}
        assert "browser_submit" not in names
        assert "browser_download" not in names

    def test_call_tool_dispatches_action_and_parses_result(self) -> None:
        provider = self.build_provider(self.fake_broker())
        card = asyncio.run(provider.list_server_cards())[0]
        result = asyncio.run(
            provider.create_client(card).call_tool(
                tool_name="browser_navigate",
                arguments={"url": "https://example.com"},
            )
        )
        assert result["status"] == "succeeded"

    def test_call_tool_cannot_dispatch_an_exact_plan_action_directly(self) -> None:
        provider = DesktopBrowserMcpProvider(
            broker_url=self.Values.BROKER_URL,
            broker_token=self.Values.TOKEN,
            runtime_context=self.context(),
            effects_enabled=True,
            http_client=self.fake_broker(),
        )
        card = asyncio.run(provider.list_server_cards())[0]

        with pytest.raises(McpConnectionError, match="staged effect"):
            asyncio.run(
                provider.create_client(card).call_tool(
                    tool_name="browser_click",
                    arguments={"elementRef": "e1_0"},
                )
            )

    def test_exact_plan_actions_are_visible_only_for_the_enforced_cohort(
        self,
    ) -> None:
        provider = DesktopBrowserMcpProvider(
            broker_url=self.Values.BROKER_URL,
            broker_token=self.Values.TOKEN,
            runtime_context=self.context(),
            effects_enabled=True,
            http_client=self.fake_broker(),
        )
        card = asyncio.run(provider.list_server_cards())[0]

        tools = asyncio.run(provider.create_client(card).list_tools())

        assert {tool.name for tool in tools} == {
            "browser_navigate",
            "browser_snapshot",
            "browser_click",
            "browser_submit",
        }
        assert {tool.name: tool.risk_level for tool in tools}[
            "browser_click"
        ].value == "high"

    def test_auth_failure_raises_typed_error(self) -> None:
        provider = self.build_provider(self.fake_broker(unauthorized=True))
        card = provider._card()
        with pytest.raises(McpAuthError):
            asyncio.run(provider.create_client(card).list_tools())

    def test_direct_call_cannot_bypass_broker_handshake(self) -> None:
        seen_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_paths.append(request.url.path)
            if request.url.path == BrowserBroker.ROUTE_HANDSHAKE:
                return httpx.Response(401, json={})
            if request.url.path == BrowserBroker.ROUTE_ACTION:
                pytest.fail("action must not run before a successful handshake")
            return httpx.Response(404, json={})

        provider = self.build_provider(
            httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        with pytest.raises(McpAuthError):
            asyncio.run(
                provider.create_client(provider._card()).call_tool(
                    tool_name="browser_navigate",
                    arguments={"url": "https://example.com"},
                )
            )
        assert seen_paths == [BrowserBroker.ROUTE_HANDSHAKE]
