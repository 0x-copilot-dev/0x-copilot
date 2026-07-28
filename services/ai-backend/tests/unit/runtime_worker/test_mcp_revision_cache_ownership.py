"""Composition-root ownership tests for the F8 discovery cache."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace

from agent_runtime.capabilities.mcp.discovery_cache import (
    McpDiscoveryCache,
    McpDiscoveryCachePort,
)
from agent_runtime.capabilities.mcp.freshness import (
    RevisionAwareMcpDiscoveryCache,
)
from agent_runtime.capabilities.mcp.revision_resolver import (
    McpDescriptorRevisionResolver,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.sse.event_bus import InMemoryEventBus
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory


def _settings(*, start_worker: bool) -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "MCP_BACKEND_REGISTRY_URL": "http://backend.test",
            "RUNTIME_STORE_BACKEND": "in_memory",
            "RUNTIME_START_IN_PROCESS_WORKER": ("true" if start_worker else "false"),
        }
    )


def _app(settings: RuntimeSettings) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            runtime_settings=settings,
            deployment=SimpleNamespace(name="single_user_desktop"),
            runtime_ports=RuntimeAdapterFactory.from_store(InMemoryRuntimeApiStore()),
            runtime_event_bus=InMemoryEventBus(),
            runtime_user_policies_resolver=None,
        )
    )


def _assert_single_composition(cache: object, *, enabled: bool) -> None:
    assert isinstance(cache, RevisionAwareMcpDiscoveryCache)
    assert isinstance(cache, McpDiscoveryCachePort)
    assert isinstance(cache._cache, McpDiscoveryCache)
    assert not isinstance(cache._cache, RevisionAwareMcpDiscoveryCache)
    assert cache._revision_checks_enabled is enabled


def test_external_worker_root_builds_exactly_one_default_off_wrapper(
    monkeypatch,
) -> None:
    monkeypatch.delenv("RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE", raising=False)
    monkeypatch.setenv("MCP_BACKEND_REGISTRY_URL", "http://backend.test")

    cache = DefaultRuntimeDependenciesFactory.build_default_discovery_cache()

    _assert_single_composition(cache, enabled=False)
    assert cache._revision_resolver is None


def test_external_worker_root_builds_shared_resolver_when_enabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE", "true")
    monkeypatch.setenv("MCP_BACKEND_REGISTRY_URL", "http://backend.test")

    cache = DefaultRuntimeDependenciesFactory.build_default_discovery_cache()

    _assert_single_composition(cache, enabled=True)
    assert isinstance(cache._revision_resolver, McpDescriptorRevisionResolver)


async def test_in_process_worker_owns_one_composition(monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE", "true")
    app = _app(_settings(start_worker=True))

    await RuntimeApiAppFactory.start_in_process_worker(app)
    task = app.state.runtime_in_process_worker_task
    try:
        _assert_single_composition(app.state.mcp_discovery_cache, enabled=True)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_api_only_process_does_not_claim_execution_cache(monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE", "true")
    app = _app(_settings(start_worker=False))

    await RuntimeApiAppFactory.start_in_process_worker(app)

    assert not hasattr(app.state, "mcp_discovery_cache")
    assert not hasattr(app.state, "runtime_in_process_worker_task")
