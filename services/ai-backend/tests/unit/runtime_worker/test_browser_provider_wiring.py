"""Gated composition of the device-local browser MCP provider (AC8).

The browser provider is composed into the ``DynamicMcpRegistry`` provider tuple
ONLY when ``RUNTIME_ENABLE_DESKTOP_BROWSER`` + ``single_user_desktop`` + a
browser broker URL/token are all present. Off that path it is absent and the
registry is byte-identical (``EmptyMcpRegistry`` when nothing else is
configured).
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.browser.constants import BrowserEnv
from agent_runtime.capabilities.browser.desktop_browser_provider import (
    DesktopBrowserMcpProvider,
)
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from agent_runtime.settings import RuntimeSettings
from runtime_worker.dependencies import (
    DefaultRuntimeDependenciesFactory,
    EmptyMcpRegistry,
)

_ON = {
    BrowserEnv.FLAG: "1",
    "ENTERPRISE_DEPLOYMENT_PROFILE": "single_user_desktop",
    BrowserEnv.BROKER_URL: "http://127.0.0.1:8842",
    BrowserEnv.BROKER_TOKEN: "browser-boot-token",
}


def _factory() -> DefaultRuntimeDependenciesFactory:
    # No MCP backend configured, so the registry is EmptyMcpRegistry unless the
    # browser provider composes in.
    return DefaultRuntimeDependenciesFactory(RuntimeSettings.load(environ={}))


def _set_env(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for key in (
        BrowserEnv.FLAG,
        "ENTERPRISE_DEPLOYMENT_PROFILE",
        BrowserEnv.BROKER_URL,
        BrowserEnv.BROKER_TOKEN,
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


class TestBrowserProviderGating:
    def test_absent_by_default(
        self, monkeypatch: pytest.MonkeyPatch, runtime_context_admin
    ) -> None:
        _set_env(monkeypatch, {})
        assert _factory()._browser_provider(runtime_context_admin) is None
        assert isinstance(
            _factory()._mcp_registry(runtime_context_admin), EmptyMcpRegistry
        )

    def test_composed_when_flag_desktop_and_broker(
        self, monkeypatch: pytest.MonkeyPatch, runtime_context_admin
    ) -> None:
        _set_env(monkeypatch, _ON)
        provider = _factory()._browser_provider(runtime_context_admin)
        assert isinstance(provider, DesktopBrowserMcpProvider)

        registry = _factory()._mcp_registry(runtime_context_admin)
        assert isinstance(registry, DynamicMcpRegistry)
        assert any(isinstance(p, DesktopBrowserMcpProvider) for p in registry.providers)

    def test_absent_off_desktop_profile(
        self, monkeypatch: pytest.MonkeyPatch, runtime_context_admin
    ) -> None:
        _set_env(monkeypatch, {**_ON, "ENTERPRISE_DEPLOYMENT_PROFILE": "server"})
        assert _factory()._browser_provider(runtime_context_admin) is None

    def test_absent_without_broker_credentials(
        self, monkeypatch: pytest.MonkeyPatch, runtime_context_admin
    ) -> None:
        env = {k: v for k, v in _ON.items() if k != BrowserEnv.BROKER_TOKEN}
        _set_env(monkeypatch, env)
        assert _factory()._browser_provider(runtime_context_admin) is None

    def test_absent_when_flag_off(
        self, monkeypatch: pytest.MonkeyPatch, runtime_context_admin
    ) -> None:
        _set_env(monkeypatch, {**_ON, BrowserEnv.FLAG: "0"})
        assert _factory()._browser_provider(runtime_context_admin) is None
