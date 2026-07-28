"""Composition-root ownership tests for the F8 discovery cache."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace

import pytest

from agent_runtime.capabilities.mcp.discovery_cache import (
    McpDiscoveryCache,
    McpDiscoveryCachePort,
)
from agent_runtime.capabilities.mcp.freshness import (
    McpDescriptorFreshnessRequest,
    McpDescriptorRevision,
    McpDescriptorSubject,
    RevisionAwareMcpDiscoveryCache,
)
from agent_runtime.capabilities.mcp.cards import (
    LoadedMcpServer,
    McpAuthMode,
    McpConnectionMetadata,
    McpServerCard,
    McpServerHealth,
    McpToolDescriptor,
    McpTransport,
)
from agent_runtime.capabilities.mcp.revision_feed import (
    InMemoryMcpRevisionCursorStore,
    McpRevisionSubject,
)
from agent_runtime.capabilities.mcp.revision_resolver import (
    McpDescriptorRevisionResolver,
)
from agent_runtime.capabilities.mcp.revision_wire import (
    BackendMcpRevisionFeed,
    BackendMcpRevisionNotice,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.sse.event_bus import InMemoryEventBus
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.mcp_revision_composition import McpRevisionControlPlaneBuilder
from runtime_adapters.file.mcp_revision_cursor import (
    DesktopFilesystemMcpRevisionCursorStore,
)


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


def _loaded() -> LoadedMcpServer:
    return LoadedMcpServer(
        server_card=McpServerCard(
            name="drive",
            short_description="test",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            health=McpServerHealth.HEALTHY,
            load_cost=1,
        ),
        tools=(
            McpToolDescriptor(
                name="search",
                description="test",
                input_schema={"type": "object"},
                output_shape={"type": "object"},
            ),
        ),
        resources=(),
        connection_metadata=McpConnectionMetadata(
            server_name="drive",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
        ),
    )


def _feed() -> BackendMcpRevisionFeed:
    return BackendMcpRevisionFeed(
        notices=(
            BackendMcpRevisionNotice.model_validate(
                {
                    "cursor": "notice-cursor",
                    "notice_id": "notice-1",
                    "sequence_no": 1,
                    "server_id": "server-drive",
                    "profile_id": "profile",
                    "subject_scope_hash": "scope",
                    "new_revision": "revision-2",
                    "reason": "config_changed",
                    "occurred_at": "2026-01-01T00:00:00Z",
                }
            ),
        ),
        next_cursor="page-cursor",
    )


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


def test_external_worker_assembly_has_one_shared_revision_graph(monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE", "true")
    monkeypatch.setenv("MCP_BACKEND_REGISTRY_URL", "http://backend.test")

    assembly = McpRevisionControlPlaneBuilder.build()

    assert assembly.enabled is True
    assert assembly.discovery_cache._cache is assembly.base_cache
    assert assembly.discovery_cache._revision_resolver is assembly.resolver
    assert assembly.discovery_cache._active_subjects is assembly.subjects
    assert assembly.resolver is not None
    assert assembly.resolver._client is assembly.revision_client
    assert assembly.coordinator is not None
    assert assembly.coordinator._resolver is assembly.resolver
    assert assembly.coordinator._cursors is assembly.cursor_store
    assert assembly.runner is not None
    assert assembly.runner._client is assembly.revision_client
    assert assembly.runner._subjects is assembly.subjects
    assert assembly.runner._coordinator is assembly.coordinator
    assert assembly.poller is not None
    assert assembly.poller._runner is assembly.runner


def test_disabled_assembly_has_no_feed_owner(monkeypatch) -> None:
    monkeypatch.delenv("RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE", raising=False)

    assembly = McpRevisionControlPlaneBuilder.build()

    _assert_single_composition(assembly.discovery_cache, enabled=False)
    assert assembly.runner is None
    assert assembly.poller is None
    assert assembly.subjects is None


async def test_enabled_assembly_has_no_active_subject_network_calls(
    monkeypatch,
) -> None:
    monkeypatch.setenv("RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE", "true")
    monkeypatch.setenv("MCP_BACKEND_REGISTRY_URL", "http://backend.test")
    assembly = McpRevisionControlPlaneBuilder.build()

    assert assembly.runner is not None
    result = await assembly.runner.run_once()

    assert result.subjects == 0
    assert result.http_calls == 0


async def test_real_shared_graph_invalidates_warmed_descriptor_before_cursor(
    monkeypatch,
) -> None:
    monkeypatch.setenv("RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE", "true")
    monkeypatch.setenv("MCP_BACKEND_REGISTRY_URL", "http://backend.test")
    assembly = McpRevisionControlPlaneBuilder.build()
    assert assembly.resolver is not None
    assert assembly.coordinator is not None
    assert assembly.catalog is not None
    assert assembly.cursor_store is not None
    subject = McpRevisionSubject(org_id="org", user_id="user")
    request = McpDescriptorFreshnessRequest(
        server_name="drive",
        subject=McpDescriptorSubject(org_id="org", user_id="user"),
        revision=McpDescriptorRevision(value="revision-1"),
    )
    await assembly.resolver.register(
        org_id="org", user_id="user", server_name="drive", server_id="server-drive"
    )
    await assembly.discovery_cache.put(request, _loaded())
    observed: list[str] = []
    original_save = assembly.cursor_store.save

    async def save_after_invalidation(target: McpRevisionSubject, cursor: str) -> None:
        assert await assembly.base_cache.get(request.cache_key()) is None
        assert (
            await assembly.catalog.generation(subject=subject, server_id="server-drive")
            == 1
        )
        observed.append(cursor)
        await original_save(target, cursor)

    assembly.cursor_store.save = save_after_invalidation  # type: ignore[method-assign]
    await assembly.coordinator.apply_page(subject=subject, feed=_feed())

    assert observed == ["page-cursor"]
    assert await assembly.cursor_store.load(subject) == "page-cursor"

    async def fail_catalog(**_kwargs: object) -> int:
        raise RuntimeError("catalog unavailable")

    assembly.catalog.advance = fail_catalog  # type: ignore[method-assign]
    other = McpRevisionSubject(org_id="other-org", user_id="other-user")
    with pytest.raises(RuntimeError, match="catalog unavailable"):
        await assembly.coordinator.apply_page(subject=other, feed=_feed())
    assert await assembly.cursor_store.load(other) is None


def test_enabled_cursor_selection_uses_file_only_for_file_store(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE", "true")
    monkeypatch.setenv("MCP_BACKEND_REGISTRY_URL", "http://backend.test")
    file_settings = SimpleNamespace(
        mcp=SimpleNamespace(backend_registry_url="http://backend.test"),
        store=SimpleNamespace(backend="file", file_store_root=str(tmp_path)),
    )
    memory_settings = SimpleNamespace(
        mcp=SimpleNamespace(backend_registry_url="http://backend.test"),
        store=SimpleNamespace(backend="in_memory", file_store_root=None),
    )

    file_assembly = McpRevisionControlPlaneBuilder.build(file_settings)  # type: ignore[arg-type]
    memory_assembly = McpRevisionControlPlaneBuilder.build(memory_settings)  # type: ignore[arg-type]

    assert isinstance(
        file_assembly.cursor_store, DesktopFilesystemMcpRevisionCursorStore
    )
    assert isinstance(memory_assembly.cursor_store, InMemoryMcpRevisionCursorStore)


def test_enabled_rejects_malformed_or_out_of_bounds_feed_config(monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE", "true")
    monkeypatch.setenv("MCP_BACKEND_REGISTRY_URL", "http://backend.test")
    monkeypatch.setenv("RUNTIME_MCP_REVISION_MAX_PAGES", "not-a-number")
    with pytest.raises(ValueError, match="RUNTIME_MCP_REVISION_MAX_PAGES"):
        McpRevisionControlPlaneBuilder.build()

    monkeypatch.setenv("RUNTIME_MCP_REVISION_MAX_PAGES", "101")
    with pytest.raises(ValueError, match="RUNTIME_MCP_REVISION_MAX_PAGES"):
        McpRevisionControlPlaneBuilder.build()


def test_enabled_external_worker_requires_backend_url(monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE", "true")
    monkeypatch.delenv("MCP_BACKEND_REGISTRY_URL", raising=False)

    with pytest.raises(ValueError, match="requires MCP_BACKEND_REGISTRY_URL"):
        DefaultRuntimeDependenciesFactory.build_default_discovery_cache()


async def test_in_process_worker_owns_one_composition(monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE", "true")
    app = _app(_settings(start_worker=True))

    await RuntimeApiAppFactory.start_in_process_worker(app)
    task = app.state.runtime_in_process_worker_task
    try:
        _assert_single_composition(app.state.mcp_discovery_cache, enabled=True)
        assembly = app.state.mcp_revision_control_plane
        assert assembly.discovery_cache is app.state.mcp_discovery_cache
        assert assembly.poller is not None
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
