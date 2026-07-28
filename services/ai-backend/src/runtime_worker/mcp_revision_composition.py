"""The single worker-owned MCP descriptor revision control-plane assembly."""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent_runtime.capabilities.mcp.discovery_cache import McpDiscoveryCache
from agent_runtime.capabilities.mcp.control_plane_metrics import (
    McpControlPlaneMetrics,
    McpControlPlaneMetricsPort,
    NoopMcpControlPlaneMetrics,
)
from agent_runtime.capabilities.mcp.freshness import (
    McpDescriptorSubject,
    RevisionAwareMcpDiscoveryCache,
)
from agent_runtime.capabilities.mcp.revision_feed import (
    ActiveMcpRevisionSubjectRegistry,
    InMemoryMcpRevisionCursorStore,
    McpCatalogGenerationAuthorityPort,
    McpDescriptorCacheInvalidationPort,
    McpRevisionCursorStorePort,
    McpRevisionFeedCoordinator,
    McpRevisionFeedRunner,
    McpRevisionSubject,
    ProcessLocalMcpCatalogGenerationAuthority,
)
from agent_runtime.capabilities.mcp.revision_resolver import (
    McpDescriptorRevisionResolver,
)
from agent_runtime.capabilities.mcp.revision_wire import BackendMcpRevisionClient
from agent_runtime.settings import RuntimeSettings
from runtime_worker.mcp_revision_poller import McpRevisionFeedPoller


class RevisionControlPlaneEnvironment:
    """Strict, bounded environment parsing for the worker-only F8 assembly."""

    ENABLED = "RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE"

    @staticmethod
    def enabled(environ: dict[str, str] | None = None) -> bool:
        raw = (environ or os.environ).get(RevisionControlPlaneEnvironment.ENABLED, "")
        normalized = raw.strip().lower()
        if not normalized:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"{RevisionControlPlaneEnvironment.ENABLED} must be a boolean")

    @staticmethod
    def positive_float(name: str, default: float, *, maximum: float) -> float:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be a number") from exc
        if not 0 < value <= maximum:
            raise ValueError(f"{name} must be > 0 and <= {maximum}")
        return value

    @staticmethod
    def positive_int(name: str, default: int, *, maximum: int) -> int:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if not 0 < value <= maximum:
            raise ValueError(f"{name} must be > 0 and <= {maximum}")
        return value


@dataclass(frozen=True, slots=True)
class McpRevisionControlPlane:
    """One process-local ownership graph, present only in worker processes."""

    base_cache: McpDiscoveryCache
    discovery_cache: RevisionAwareMcpDiscoveryCache
    revision_client: BackendMcpRevisionClient | None
    resolver: McpDescriptorRevisionResolver | None
    subjects: ActiveMcpRevisionSubjectRegistry | None
    cursor_store: McpRevisionCursorStorePort | None
    catalog: McpCatalogGenerationAuthorityPort | None
    invalidator: McpDescriptorCacheInvalidationPort | None
    coordinator: McpRevisionFeedCoordinator | None
    runner: McpRevisionFeedRunner | None
    poller: McpRevisionFeedPoller | None
    metrics: McpControlPlaneMetricsPort

    @property
    def enabled(self) -> bool:
        return self.runner is not None


class RevisionAwareMcpDescriptorCacheInvalidator:
    """Feed invalidator over the same descriptor cache and resolver assembly."""

    def __init__(
        self,
        *,
        cache: RevisionAwareMcpDiscoveryCache,
        resolver: McpDescriptorRevisionResolver,
    ) -> None:
        self._cache = cache
        self._resolver = resolver

    async def invalidate_descriptor(
        self, *, subject: McpRevisionSubject, server_id: str, notice_id: str
    ) -> None:
        del notice_id
        names = await self._resolver.registered_server_names(
            org_id=subject.org_id,
            user_id=subject.user_id,
            server_id=server_id,
        )
        for server_name in names:
            await self._cache.invalidate_descriptor_subject(
                McpDescriptorSubject(org_id=subject.org_id, user_id=subject.user_id),
                server_name=server_name,
            )

    async def flush_subject(self, *, subject: McpRevisionSubject) -> None:
        await self._cache.invalidate_descriptor_subject(
            McpDescriptorSubject(org_id=subject.org_id, user_id=subject.user_id)
        )


class McpRevisionControlPlaneBuilder:
    """Build one graph for an external or in-process runtime worker."""

    @classmethod
    def build(cls, settings: RuntimeSettings | None = None) -> McpRevisionControlPlane:
        env = RevisionControlPlaneEnvironment
        base = McpDiscoveryCache(
            ttl_seconds=env.positive_float(
                "RUNTIME_MCP_DISCOVERY_CACHE_TTL_SECONDS", 900, maximum=86_400
            ),
            max_entries=env.positive_int(
                "RUNTIME_MCP_DISCOVERY_CACHE_MAX_ENTRIES", 1000, maximum=100_000
            ),
        )
        enabled = env.enabled()
        if not enabled:
            return McpRevisionControlPlane(
                base_cache=base,
                discovery_cache=RevisionAwareMcpDiscoveryCache(
                    base,
                    max_staleness_seconds=env.positive_float(
                        "RUNTIME_MCP_DESCRIPTOR_MAX_STALENESS_SECONDS",
                        300,
                        maximum=86_400,
                    ),
                ),
                revision_client=None,
                resolver=None,
                subjects=None,
                cursor_store=None,
                catalog=None,
                invalidator=None,
                coordinator=None,
                runner=None,
                poller=None,
                metrics=NoopMcpControlPlaneMetrics(),
            )
        metrics = McpControlPlaneMetrics()
        backend_url = (
            settings.mcp.backend_registry_url
            if settings is not None
            else os.environ.get("MCP_BACKEND_REGISTRY_URL", "").strip() or None
        )
        if backend_url is None:
            raise ValueError(
                "RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE requires MCP_BACKEND_REGISTRY_URL"
            )
        revision_client = BackendMcpRevisionClient(backend_url=backend_url)
        resolver = McpDescriptorRevisionResolver(
            revision_client,
            ttl_seconds=env.positive_float(
                "RUNTIME_MCP_REVISION_CACHE_TTL_SECONDS", 30, maximum=86_400
            ),
            max_entries=env.positive_int(
                "RUNTIME_MCP_REVISION_CACHE_MAX_ENTRIES", 1000, maximum=100_000
            ),
            metrics=metrics,
        )
        subjects = ActiveMcpRevisionSubjectRegistry(
            max_subjects=env.positive_int(
                "RUNTIME_MCP_REVISION_SUBJECT_MAX", 256, maximum=10_000
            ),
            inactivity_ttl_seconds=env.positive_float(
                "RUNTIME_MCP_REVISION_SUBJECT_TTL_SECONDS", 300, maximum=86_400
            ),
            metrics=metrics,
        )
        cache = RevisionAwareMcpDiscoveryCache(
            base,
            max_staleness_seconds=env.positive_float(
                "RUNTIME_MCP_DESCRIPTOR_MAX_STALENESS_SECONDS", 300, maximum=86_400
            ),
            revision_resolver=resolver,
            revision_checks_enabled=True,
            active_subjects=subjects,
            metrics=metrics,
        )
        cursors = cls._cursor_store(settings)
        catalog = ProcessLocalMcpCatalogGenerationAuthority(
            max_notices=env.positive_int(
                "RUNTIME_MCP_REVISION_MAX_DEDUPE_NOTICES", 4096, maximum=100_000
            ),
            max_entries=env.positive_int(
                "RUNTIME_MCP_REVISION_CATALOG_MAX_ENTRIES", 1000, maximum=100_000
            ),
        )
        invalidator = RevisionAwareMcpDescriptorCacheInvalidator(
            cache=cache, resolver=resolver
        )
        coordinator = McpRevisionFeedCoordinator(
            resolver=resolver,
            descriptors=invalidator,
            catalog=catalog,
            cursors=cursors,
            max_dedupe_notices=env.positive_int(
                "RUNTIME_MCP_REVISION_MAX_DEDUPE_NOTICES", 4096, maximum=100_000
            ),
            metrics=metrics,
        )
        backoff_base_seconds = env.positive_float(
            "RUNTIME_MCP_REVISION_BACKOFF_BASE_SECONDS", 1, maximum=3600
        )
        backoff_max_seconds = env.positive_float(
            "RUNTIME_MCP_REVISION_BACKOFF_MAX_SECONDS", 60, maximum=86_400
        )
        # Validate ordering rather than silently swapping explicitly supplied
        # values; a malformed deployment must retain its observable intent.
        if backoff_max_seconds < backoff_base_seconds:
            raise ValueError(
                "RUNTIME_MCP_REVISION_BACKOFF_MAX_SECONDS must be at least "
                "RUNTIME_MCP_REVISION_BACKOFF_BASE_SECONDS"
            )
        runner = McpRevisionFeedRunner(
            client=revision_client,
            subjects=subjects,
            cursors=cursors,
            coordinator=coordinator,
            max_pages=env.positive_int(
                "RUNTIME_MCP_REVISION_MAX_PAGES", 4, maximum=100
            ),
            max_notices=env.positive_int(
                "RUNTIME_MCP_REVISION_MAX_NOTICES", 200, maximum=10_000
            ),
            max_bytes=env.positive_int(
                "RUNTIME_MCP_REVISION_MAX_BYTES", 65536, maximum=10_485_760
            ),
            page_limit=env.positive_int(
                "RUNTIME_MCP_REVISION_PAGE_LIMIT", 100, maximum=100
            ),
            backoff_base_seconds=backoff_base_seconds,
            backoff_max_seconds=backoff_max_seconds,
            metrics=metrics,
        )
        poller = McpRevisionFeedPoller(
            runner=runner,
            interval_seconds=env.positive_float(
                "RUNTIME_MCP_REVISION_POLL_INTERVAL_SECONDS", 15, maximum=3600
            ),
            stop_grace_seconds=env.positive_float(
                "RUNTIME_MCP_REVISION_STOP_GRACE_SECONDS", 5, maximum=60
            ),
            metrics=metrics,
        )
        return McpRevisionControlPlane(
            base_cache=base,
            discovery_cache=cache,
            revision_client=revision_client,
            resolver=resolver,
            subjects=subjects,
            cursor_store=cursors,
            catalog=catalog,
            invalidator=invalidator,
            coordinator=coordinator,
            runner=runner,
            poller=poller,
            metrics=metrics,
        )

    @staticmethod
    def _cursor_store(settings: RuntimeSettings | None) -> McpRevisionCursorStorePort:
        file_backend = (
            settings.store.backend == "file"
            if settings is not None
            else os.environ.get("RUNTIME_STORE_BACKEND", "").strip().lower() == "file"
        )
        if file_backend:
            root = (
                settings.store.file_store_root
                if settings is not None
                else os.environ.get("RUNTIME_FILE_STORE_ROOT", "").strip() or None
            )
            if not root:
                raise ValueError(
                    "RUNTIME_FILE_STORE_ROOT is required for MCP revision cursors"
                )
            from runtime_adapters.file.mcp_revision_cursor import (
                DesktopFilesystemMcpRevisionCursorStore,
            )

            # The adapter validates platform support and filesystem safety at
            # construction; enabled startup fails honestly instead of falling
            # back to a volatile cursor on desktop.
            return DesktopFilesystemMcpRevisionCursorStore(root)
        return InMemoryMcpRevisionCursorStore()
