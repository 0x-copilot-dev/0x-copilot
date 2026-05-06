"""Membership resolver wiring in :class:`RuntimeApiAppFactory` (PR 1.4.1).

The factory composes :class:`HttpWorkspaceMembershipResolver` when the
trusted backend lane is configured (``BACKEND_BASE_URL`` +
``ENTERPRISE_SERVICE_TOKEN``) and falls back to the empty in-memory
resolver otherwise. Tests bypass the picker by injecting their own
resolver, but the picker itself needs coverage so we don't silently
revert to "deny everything" in production again.
"""

from __future__ import annotations

import pytest

from agent_runtime.api.membership import (
    HttpWorkspaceMembershipResolver,
    InMemoryWorkspaceMembershipResolver,
)
from runtime_api.app import RuntimeApiAppFactory


class TestDefaultMembershipResolverPicker:
    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BACKEND_BASE_URL", raising=False)
        monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)

    def test_returns_in_memory_when_no_backend_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok")
        resolver = RuntimeApiAppFactory.default_membership_resolver()
        assert isinstance(resolver, InMemoryWorkspaceMembershipResolver)

    def test_returns_in_memory_when_no_service_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BACKEND_BASE_URL", "http://backend:8100")
        resolver = RuntimeApiAppFactory.default_membership_resolver()
        assert isinstance(resolver, InMemoryWorkspaceMembershipResolver)

    def test_returns_in_memory_when_backend_url_is_blank(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BACKEND_BASE_URL", "   ")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok")
        resolver = RuntimeApiAppFactory.default_membership_resolver()
        assert isinstance(resolver, InMemoryWorkspaceMembershipResolver)

    def test_returns_http_when_both_env_vars_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BACKEND_BASE_URL", "http://backend:8100")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok")
        resolver = RuntimeApiAppFactory.default_membership_resolver()
        assert isinstance(resolver, HttpWorkspaceMembershipResolver)

    def test_in_memory_default_denies_all_membership_checks(self) -> None:
        # Sanity: the conservative-deny fallback the picker uses must
        # actually deny — otherwise unconfigured deploys would auto-grant
        # cross-user writes. ``is_active_member`` returns False for any
        # (org, user) the resolver wasn't seeded with.
        import asyncio

        resolver = RuntimeApiAppFactory.default_membership_resolver()
        assert isinstance(resolver, InMemoryWorkspaceMembershipResolver)
        assert (
            asyncio.run(resolver.is_active_member(org_id="org_x", user_id="user_y"))
            is False
        )
