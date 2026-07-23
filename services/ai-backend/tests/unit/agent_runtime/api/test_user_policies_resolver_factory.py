"""The trusted-backend lane is all-or-nothing.

``UserPoliciesResolverFactory`` picks the resolver from environment config. A
lane that is *half* configured (exactly one of ``BACKEND_BASE_URL`` /
``ENTERPRISE_SERVICE_TOKEN``) silently degrades every user's decrypted BYOK
provider keys to empty, so every BYOK run fails at create with a misleading "add
a key". The factory therefore fails loud at wiring time on partial config rather
than returning a Null resolver that drops keys per-run. These tests pin that
contract — including the regression that let self-host prod ship the lane with a
token but no URL.
"""

from __future__ import annotations

import httpx
import pytest

from agent_runtime.api.user_policies_resolver import (
    HttpUserPoliciesResolver,
    NullUserPoliciesResolver,
    TrustedBackendLaneError,
    UserPoliciesResolverFactory,
)

_URL = "BACKEND_BASE_URL"
_TOKEN = "ENTERPRISE_SERVICE_TOKEN"


def _client() -> httpx.AsyncClient:
    # The factory never calls the client (it only checks presence), so a
    # MockTransport keeps it inert and non-networking.
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _req: httpx.Response(200, json={})),
        base_url="http://backend",
    )


@pytest.fixture(autouse=True)
def _clear_lane_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test from a lane-unset baseline regardless of the host env."""
    monkeypatch.delenv(_URL, raising=False)
    monkeypatch.delenv(_TOKEN, raising=False)


class TestUserPoliciesResolverFactory:
    """All-or-nothing: both → HTTP, neither → Null, exactly one → fail loud."""

    def test_neither_set_returns_null(self) -> None:
        resolver = UserPoliciesResolverFactory.default(http_client=_client())
        assert isinstance(resolver, NullUserPoliciesResolver)

    def test_both_set_returns_http(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_URL, "http://backend:8100")
        monkeypatch.setenv(_TOKEN, "svc-token")
        resolver = UserPoliciesResolverFactory.default(http_client=_client())
        assert isinstance(resolver, HttpUserPoliciesResolver)

    def test_only_url_set_fails_loud(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_URL, "http://backend:8100")
        with pytest.raises(TrustedBackendLaneError):
            UserPoliciesResolverFactory.default(http_client=_client())

    def test_only_token_set_fails_loud(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The exact self-host prod regression: token present, URL missing.
        monkeypatch.setenv(_TOKEN, "svc-token")
        with pytest.raises(TrustedBackendLaneError):
            UserPoliciesResolverFactory.default(http_client=_client())

    def test_configured_without_client_fails_loud(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Config present but no client is a wiring bug — fail loud, never Null.
        monkeypatch.setenv(_URL, "http://backend:8100")
        monkeypatch.setenv(_TOKEN, "svc-token")
        with pytest.raises(TrustedBackendLaneError):
            UserPoliciesResolverFactory.default()
