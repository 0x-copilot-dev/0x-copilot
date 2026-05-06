"""FastAPI app composition for the runtime API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from agent_runtime.api.service import RuntimeApiService
from agent_runtime.deployment import (
    DeploymentProfile,
    log_profile,
    resolve_or_exit,
)
from agent_runtime.observability.http_logging import (
    LoggingConfigurator,
    RequestContextMiddleware,
)
from agent_runtime.observability.otel import TelemetryBootstrap
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_api.http.errors import RuntimeApiError, RuntimeApiErrorMapper
from runtime_api.http.retention_routes import (
    RetentionAdminRouter,
    RetentionMemberRouter,
)
from runtime_api.http.routes import (
    BudgetApiRouter,
    InternalRuntimeApiRouter,
    RuntimeApiRouter,
    UsageApiRouter,
)
from runtime_api.rbac import public_route
from runtime_api.routes.health import register_health_routes
from runtime_api.sse.event_bus import RuntimeEventBus
from runtime_worker import RuntimeWorker


_ASYNC_BACKENDS = frozenset({"in_memory_async", "postgres"})


class RuntimeApiAppFactory:
    """Create a FastAPI app with dependency-inverted runtime API ports."""

    @classmethod
    def create_app(
        cls,
        service: RuntimeApiService | None = None,
        *,
        configure_logging_on_create: bool = True,
        configure_telemetry_on_create: bool = True,
        deployment: DeploymentProfile | None = None,
    ) -> FastAPI:
        if configure_logging_on_create:
            LoggingConfigurator.configure()
        if configure_telemetry_on_create:
            TelemetryBootstrap.configure()
            TelemetryBootstrap.instrument_httpx_clients()
        resolved_deployment = deployment or resolve_or_exit()
        log_profile(resolved_deployment)

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            await cls.open_async_store(app)
            await cls.start_in_process_worker(app)
            try:
                yield
            finally:
                await cls.stop_in_process_worker(app)
                await cls.close_async_store(app)

        app = FastAPI(title="Agent Runtime API", version="1", lifespan=lifespan)
        app.add_middleware(RequestContextMiddleware)
        if configure_telemetry_on_create:
            TelemetryBootstrap.instrument_fastapi(app)
        configured_service = service or cls.default_service(app)
        app.state.runtime_api_service = configured_service
        app.state.deployment = resolved_deployment
        app.state.draft_service = cls.default_draft_service(app)
        app.state.workspace_feed_service = cls.default_workspace_feed_service(app)
        # PR 6.1 — share_service composes ShareStore + persistence + event
        # store + workspace_feed (sources tab) + draft_service (drafts).
        # MUST run before ``default_conversation_fork_service`` because it
        # also registers itself as ``app.state.share_snapshot_port`` —
        # PR 6.2's fork service depends on that port.
        app.state.share_service = cls.default_share_service(app)
        if app.state.share_service is not None:
            app.state.share_snapshot_port = app.state.share_service
        # PR 6.2 — conversation fork service. The share-snapshot port is
        # owned by PR 6.1 (registered above). Tests can override by
        # wiring ``app.state.conversation_fork_service`` directly.
        app.state.conversation_fork_service = cls.default_conversation_fork_service(app)

        @app.get("/v1/health", dependencies=[Depends(public_route())])
        async def health() -> dict[str, object]:
            return {
                "service": "ai-backend",
                "deployment_profile": resolved_deployment.name,
                "feature_toggles_hash": resolved_deployment.toggles_hash(),
            }

        app.include_router(RuntimeApiRouter.create_router())
        app.include_router(UsageApiRouter.create_router())
        app.include_router(BudgetApiRouter.create_router())
        app.include_router(RetentionAdminRouter.create_router())
        app.include_router(RetentionMemberRouter.create_router())
        app.include_router(InternalRuntimeApiRouter.create_router())
        app.add_exception_handler(
            RuntimeApiError, RuntimeApiErrorMapper.handle_runtime_api_error
        )
        app.add_exception_handler(
            ValidationError, RuntimeApiErrorMapper.handle_validation_error
        )
        app.add_exception_handler(
            RequestValidationError,
            RuntimeApiErrorMapper.handle_request_validation_error,
        )
        app.add_exception_handler(
            Exception, RuntimeApiErrorMapper.handle_unexpected_error
        )
        register_health_routes(app)
        return app

    @classmethod
    def default_service(cls, app: FastAPI) -> RuntimeApiService:
        settings = RuntimeSettings.load()
        RuntimeSettings.configure_sdk_environment(settings)
        event_bus = RuntimeEventBus.get_default()
        # PR 1.4.1 — production wiring for the inbox bus + the
        # composite notification dispatcher. Tests use the
        # ``RuntimeApiService(notification_dispatcher=...)`` constructor
        # arg directly so this app-factory wire is the only place
        # production composes them.
        from runtime_api.sse.inbox_bus import InboxEventBus
        from agent_runtime.api.notifications import (
            InboxAndEmailNotificationDispatcher,
            LoggingNotificationDispatcher,
        )

        inbox_bus = InboxEventBus.get_default()
        app.state.runtime_inbox_bus = inbox_bus

        async def _inbox_publish(approval, event_type, actor_user_id):
            await inbox_bus.publish(
                user_id=approval.user_id,
                event_type=event_type,
                approval_id=approval.approval_id,
                status=approval.status.value,
                org_id=approval.org_id,
                conversation_id=approval.conversation_id,
                actor_user_id=actor_user_id,
            )

        # Production dispatcher fans out to the inbox bus + (when wired
        # by env flag) the email channel. Email + the HTTP poster are
        # plumbed in W4.1 alongside the notification matrix; until then
        # we ship inbox-only and log emails via the logging fallback.
        notification_dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=_inbox_publish,
            post=None,
        )
        # The membership resolver default is also the in-memory impl
        # (reject everything) — production wires the HTTP impl in a
        # follow-up that depends on the backend's identity client; the
        # Phase A landing of this PR keeps the wire surface but leaves
        # the production HTTP injection to the deployment harness.
        # Tests wire ``InMemoryWorkspaceMembershipResolver`` directly.
        app.state.runtime_settings = settings
        app.state.runtime_event_bus = event_bus
        if settings.store.backend in _ASYNC_BACKENDS:
            async_ports = RuntimeAdapterFactory.async_from_settings(settings)
            app.state.async_runtime_ports = async_ports
            return RuntimeApiService(
                persistence=async_ports.persistence,
                event_store=async_ports.event_store,
                queue=async_ports.queue,
                settings=settings,
                on_event_appended=event_bus.notify_sync,
                notification_dispatcher=notification_dispatcher
                if isinstance(
                    notification_dispatcher, InboxAndEmailNotificationDispatcher
                )
                else LoggingNotificationDispatcher(),
            )
        ports = RuntimeAdapterFactory.from_settings(settings)
        app.state.runtime_ports = ports
        return RuntimeApiService(
            persistence=ports.persistence,
            event_store=ports.event_store,
            queue=ports.queue,
            settings=settings,
            on_event_appended=event_bus.notify_sync,
            notification_dispatcher=notification_dispatcher,
        )

    @classmethod
    def default_draft_service(cls, app):
        """Wire the Workspace-pane draft service for the configured backend.

        PR 1.3.5 wires:
        - DraftStore (in-memory or Postgres-backed)
        - PersistencePort (for approval-row insert + audit chain)
        - CapabilityAuthGate (pre-check on POST /send)
        - RuntimeEventProducer (emits APPROVAL_REQUESTED on the host run's
          stream so the FE renders the inline approval card)
        """

        from agent_runtime.api.draft_service import DraftService
        from agent_runtime.api.events import RuntimeEventProducer
        from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
        from runtime_adapters.postgres.draft_store import PostgresDraftStore

        async_ports = getattr(app.state, "async_runtime_ports", None)
        ports = getattr(app.state, "runtime_ports", None)
        if async_ports is not None and async_ports.backend == "postgres":
            store = PostgresDraftStore(async_ports.store)
            persistence = async_ports.persistence
            event_store = async_ports.event_store
        elif async_ports is not None:
            store = (
                async_ports.draft_store
                if async_ports.draft_store is not None
                else InMemoryDraftStore()
            )
            persistence = async_ports.persistence
            event_store = async_ports.event_store
        elif ports is not None:
            store = (
                ports.draft_store
                if ports.draft_store is not None
                else InMemoryDraftStore()
            )
            persistence = ports.persistence
            event_store = ports.event_store
        else:  # pragma: no cover — only hit when the app boots without ports
            return DraftService(store=InMemoryDraftStore())

        event_producer = RuntimeEventProducer(
            persistence=persistence,
            event_store=event_store,
        )
        auth_gate = cls._draft_auth_gate(app)
        return DraftService(
            store=store,
            persistence=persistence,
            auth_gate=auth_gate,
            event_producer=event_producer,
        )

    @classmethod
    def _draft_auth_gate(cls, app):  # type: ignore[no-untyped-def]
        """Build a CapabilityAuthGate from the configured runtime registries.

        Falls back to ``None`` when registries are not exposed on the app
        state (e.g. minimal test apps); DraftService degrades open in that
        case rather than rejecting every send.
        """

        from agent_runtime.capabilities.auth_gate import CapabilityAuthGate

        tool_registry = getattr(app.state, "runtime_tool_registry", None)
        mcp_registry = getattr(app.state, "runtime_mcp_registry", None)
        if tool_registry is None or mcp_registry is None:
            return None
        return CapabilityAuthGate(
            tool_registry=tool_registry, mcp_registry=mcp_registry
        )

    @classmethod
    def default_share_service(cls, app: FastAPI):
        """Wire :class:`ShareService` (PR 6.1).

        The share service backs:

        - the creator surface (``POST /v1/agent/conversations/{id}/share``,
          list / patch / revoke),
        - the recipient view (``GET /v1/agent/shares/{share_token}``),
        - PR 6.2's fork service via ``ShareSnapshotPort.resolve_by_token``.

        Returns ``None`` when no ports are wired (minimal test apps).
        Production/dev always have either sync or async ports configured.
        """

        from agent_runtime.api.share_service import ShareService
        from runtime_adapters.in_memory.share_store import InMemoryShareStore

        async_ports = getattr(app.state, "async_runtime_ports", None)
        ports = getattr(app.state, "runtime_ports", None) or async_ports
        if ports is None:  # pragma: no cover — only hit when boot has no ports
            return None
        share_store = getattr(ports, "share_store", None) or InMemoryShareStore()
        api_service = getattr(app.state, "runtime_api_service", None)
        if api_service is None:
            return None
        # Construct using the runtime API service's already-adapted
        # async ports — no double-wrapping (the service's __init__ ran
        # ``adapt_persistence_to_async`` once).
        import os as _os

        return ShareService(
            store=share_store,
            persistence=api_service.persistence,
            event_store=api_service.event_store,
            workspace_feed_service=getattr(app.state, "workspace_feed_service", None),
            draft_service=getattr(app.state, "draft_service", None),
            notifications=getattr(api_service, "_notifications", None),
            app_base_url=_os.environ.get("RUNTIME_APP_BASE_URL", "").strip(),
        )

    @classmethod
    def default_conversation_fork_service(cls, app: FastAPI):
        """Wire :class:`ConversationForkService` (PR 6.2).

        Returns ``None`` when the share-snapshot port is not configured
        on app state — the fork route then surfaces 503 to callers and
        the FE renders a degraded state. PR 6.1 wires
        ``app.state.share_snapshot_port`` once the share lifecycle ships;
        until then tests are the only callers and they wire the service
        directly via ``app.state.conversation_fork_service``.
        """

        share_snapshot_port = getattr(app.state, "share_snapshot_port", None)
        if share_snapshot_port is None:
            return None

        from agent_runtime.api.conversation_fork import ConversationForkService
        from runtime_api.identity import RuntimeIdentity  # noqa: F401 (typing only)
        from runtime_worker.audit import WorkerAuditEmitter

        async_ports = getattr(app.state, "async_runtime_ports", None)
        ports = getattr(app.state, "runtime_ports", None) or async_ports
        if ports is None:  # pragma: no cover — only hit when app boots without ports
            return None

        # Reuse the runtime API service's persistence + notifications,
        # so audit + inbox fan-out share the same writers as every
        # other privileged action in this process.
        api_service = getattr(app.state, "runtime_api_service", None)
        if api_service is None:
            return None
        return ConversationForkService(
            persistence=api_service.persistence,
            share_snapshots=share_snapshot_port,
            audit=WorkerAuditEmitter(api_service.persistence),
            notifications=api_service._notifications,
        )

    @classmethod
    def default_workspace_feed_service(cls, app: FastAPI):
        """Wire the Workspace pane data feeds for the configured backend (PR 1.5)."""

        from agent_runtime.api.workspace_feed_service import WorkspaceFeedService
        from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
        from runtime_adapters.in_memory.source_store import InMemorySourceStore
        from runtime_adapters.in_memory.subagent_store import InMemorySubagentStore
        from runtime_adapters.postgres.source_store import PostgresSourceStore
        from runtime_adapters.postgres.subagent_store import PostgresSubagentStore

        async_ports = getattr(app.state, "async_runtime_ports", None)
        if async_ports is not None and async_ports.backend == "postgres":
            parent = async_ports.store
            return WorkspaceFeedService(
                subagent_store=PostgresSubagentStore(parent),
                source_store=PostgresSourceStore(parent),
            )
        ports = getattr(app.state, "runtime_ports", None) or async_ports
        underlying = (
            ports.store.underlying  # type: ignore[union-attr]
            if hasattr(getattr(ports, "store", None), "underlying")
            else getattr(ports, "store", None)
        )
        return WorkspaceFeedService(
            subagent_store=InMemorySubagentStore(underlying),
            source_store=InMemorySourceStore(InMemoryCitationStore()),
        )

    @classmethod
    async def open_async_store(cls, app: FastAPI) -> None:
        """Open + migrate the async store on startup if one was configured."""

        async_ports = getattr(app.state, "async_runtime_ports", None)
        if async_ports is None:
            return
        await async_ports.store.open()
        await async_ports.store.migrate()

    @classmethod
    async def close_async_store(cls, app: FastAPI) -> None:
        """Close the async store on shutdown."""

        async_ports = getattr(app.state, "async_runtime_ports", None)
        if async_ports is None:
            return
        await async_ports.store.close()

    @classmethod
    async def start_in_process_worker(cls, app: FastAPI) -> None:
        """Run a same-process worker for local in-memory debugging."""

        settings = getattr(app.state, "runtime_settings", None)
        if settings is None:
            return
        if settings.store.backend not in {"in_memory", "in_memory_async"}:
            return
        if not settings.execution.start_in_process_worker:
            return
        ports = getattr(app.state, "runtime_ports", None) or getattr(
            app.state, "async_runtime_ports", None
        )
        if ports is None:
            return
        event_bus = getattr(app.state, "runtime_event_bus", None)
        worker = RuntimeWorker(
            persistence=ports.persistence,
            event_store=ports.event_store,
            queue=ports.queue,
            settings=settings,
            lock_seconds=settings.execution.worker_lock_seconds,
            on_event_appended=event_bus.notify_sync if event_bus else None,
            draft_store=getattr(ports, "draft_store", None),
        )
        app.state.runtime_in_process_worker = worker
        app.state.runtime_in_process_worker_task = asyncio.create_task(
            worker.run_forever(
                poll_interval_seconds=settings.execution.worker_poll_interval_seconds,
            )
        )

    @classmethod
    async def stop_in_process_worker(cls, app: FastAPI) -> None:
        task = getattr(app.state, "runtime_in_process_worker_task", None)
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = RuntimeApiAppFactory.create_app()
