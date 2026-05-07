"""PR 4.4.7 Phase 2 (Slice B) — HTTP resolver for the run-start
suggestible-connectors fetch.

The resolver is the bridge between ai-backend run-create and the
backend's ``/internal/v1/me/suggestible-connectors`` route. Its
non-functional contract is "always return a tuple — empty on any
failure" so a flaky backend never breaks run-start. These tests pin
both the happy path and every failure mode the implementation
explicitly catches.
"""

from __future__ import annotations

import asyncio

import httpx

from agent_runtime.api.suggestible_connectors_resolver import (
    HttpSuggestibleConnectorsResolver,
    NullSuggestibleConnectorsResolver,
    SuggestibleConnectorsResolverFactory,
)


def _client(transport: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport, base_url="http://backend")


def _resolver(*, transport: httpx.MockTransport) -> HttpSuggestibleConnectorsResolver:
    return HttpSuggestibleConnectorsResolver(
        http_client=_client(transport),
        backend_url="http://backend",
        service_token="svc-test-token",
    )


class TestHttpSuggestibleConnectorsResolver:
    def test_happy_path_parses_entries_into_typed_cards(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["service_token"] = request.headers.get(
                "x-enterprise-service-token", ""
            )
            captured["org"] = request.headers.get("x-enterprise-org-id", "")
            captured["user"] = request.headers.get("x-enterprise-user-id", "")
            return httpx.Response(
                200,
                json={
                    "entries": [
                        {
                            "slug": "linear",
                            "display_name": "Linear",
                            "url": "https://mcp.linear.app/mcp",
                            "transport": "http",
                            "auth_mode": "oauth2",
                            "description": "Issues, projects, and cycles.",
                            "scopes_summary": "Read issues, projects, cycles.",
                            "brand_color": "#5E6AD2",
                            "default_scopes": [],
                            "requires_pre_registered_client": False,
                            "verified": True,
                            "discoverable": True,
                        }
                    ]
                },
            )

        resolver = _resolver(transport=httpx.MockTransport(handler))
        cards = asyncio.run(
            resolver.resolve(
                org_id="org_acme",
                user_id="user_sarah",
                exclude_paused=("seed:notion", "seed:slack"),
            )
        )

        assert len(cards) == 1
        assert cards[0].slug == "linear"
        assert cards[0].display_name == "Linear"
        assert cards[0].brand_color == "#5E6AD2"
        # The wire layer must carry the trusted-backend headers + the
        # exclude_paused query in a stable shape so the backend joins
        # the right paused set.
        assert "exclude_paused=seed%3Anotion%2Cseed%3Aslack" in captured["url"]
        assert captured["service_token"] == "svc-test-token"
        assert captured["org"] == "org_acme"
        assert captured["user"] == "user_sarah"

    def test_empty_entries_yields_empty_tuple(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"entries": []})

        resolver = _resolver(transport=httpx.MockTransport(handler))
        cards = asyncio.run(
            resolver.resolve(org_id="o", user_id="u", exclude_paused=())
        )
        assert cards == ()

    def test_non_2xx_returns_empty_tuple(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="upstream busy")

        resolver = _resolver(transport=httpx.MockTransport(handler))
        cards = asyncio.run(
            resolver.resolve(org_id="o", user_id="u", exclude_paused=())
        )
        assert cards == ()

    def test_connect_error_returns_empty_tuple(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        resolver = _resolver(transport=httpx.MockTransport(handler))
        cards = asyncio.run(
            resolver.resolve(org_id="o", user_id="u", exclude_paused=())
        )
        assert cards == ()

    def test_timeout_returns_empty_tuple(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

        resolver = _resolver(transport=httpx.MockTransport(handler))
        cards = asyncio.run(
            resolver.resolve(org_id="o", user_id="u", exclude_paused=())
        )
        assert cards == ()

    def test_malformed_json_returns_empty_tuple(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text="not json", headers={"content-type": "application/json"}
            )

        resolver = _resolver(transport=httpx.MockTransport(handler))
        cards = asyncio.run(
            resolver.resolve(org_id="o", user_id="u", exclude_paused=())
        )
        assert cards == ()

    def test_payload_missing_entries_returns_empty_tuple(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unrelated": "shape"})

        resolver = _resolver(transport=httpx.MockTransport(handler))
        cards = asyncio.run(
            resolver.resolve(org_id="o", user_id="u", exclude_paused=())
        )
        assert cards == ()

    def test_individual_malformed_row_does_not_poison_the_set(self) -> None:
        # A single bad row (e.g. missing slug) must not drop the rest.
        # The resolver's contract is "best-effort tuple of valid cards".
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "entries": [
                        {"display_name": "Bad — no slug"},
                        {
                            "slug": "linear",
                            "display_name": "Linear",
                            "description": "Issues, projects, and cycles.",
                        },
                    ]
                },
            )

        resolver = _resolver(transport=httpx.MockTransport(handler))
        cards = asyncio.run(
            resolver.resolve(org_id="o", user_id="u", exclude_paused=())
        )
        assert len(cards) == 1
        assert cards[0].slug == "linear"


class TestNullSuggestibleConnectorsResolver:
    def test_always_returns_empty_tuple(self) -> None:
        cards = asyncio.run(
            NullSuggestibleConnectorsResolver().resolve(
                org_id="o", user_id="u", exclude_paused=("seed:linear",)
            )
        )
        assert cards == ()


class TestSuggestibleConnectorsResolverFactory:
    def test_returns_null_resolver_when_no_backend_url_configured(
        self, monkeypatch
    ) -> None:
        monkeypatch.delenv("BACKEND_BASE_URL", raising=False)
        monkeypatch.delenv("MCP_BACKEND_REGISTRY_URL", raising=False)
        monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)
        resolver = SuggestibleConnectorsResolverFactory.default()
        assert isinstance(resolver, NullSuggestibleConnectorsResolver)

    def test_returns_http_resolver_with_per_call_clients_when_no_client_passed(
        self, monkeypatch
    ) -> None:
        # PR 4.4.7 — the factory builds a working resolver even when no
        # shared ``http_client`` is passed; the resolver creates a
        # per-call short-lived client. The previous behaviour (Null
        # fallback) meant production never wired the resolver because
        # nothing in ``RuntimeApiAppFactory`` constructed an
        # AsyncClient. ``RuntimeApiAppFactory.create_service`` calls
        # this code path with no http_client argument; without this
        # change the agent never sees catalog suggestions.
        monkeypatch.setenv("BACKEND_BASE_URL", "http://backend")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "svc-test")
        resolver = SuggestibleConnectorsResolverFactory.default()
        assert isinstance(resolver, HttpSuggestibleConnectorsResolver)

    def test_falls_back_to_mcp_backend_registry_url_in_dev(self, monkeypatch) -> None:
        # ``make dev`` only sets ``MCP_BACKEND_REGISTRY_URL`` for the
        # ai-backend process. Without the fallback the factory would
        # return Null and the agent would never see catalog
        # suggestions in dev. Pin the fallback so dev parity with prod
        # stays load-bearing.
        monkeypatch.delenv("BACKEND_BASE_URL", raising=False)
        monkeypatch.setenv("MCP_BACKEND_REGISTRY_URL", "http://127.0.0.1:8100")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "svc-test")
        resolver = SuggestibleConnectorsResolverFactory.default()
        assert isinstance(resolver, HttpSuggestibleConnectorsResolver)

    def test_returns_http_resolver_when_fully_configured(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKEND_BASE_URL", "http://backend")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "svc-test")
        client = httpx.AsyncClient()
        try:
            resolver = SuggestibleConnectorsResolverFactory.default(http_client=client)
            assert isinstance(resolver, HttpSuggestibleConnectorsResolver)
        finally:
            asyncio.run(client.aclose())
