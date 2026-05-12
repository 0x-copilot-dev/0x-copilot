"""FastAPI app composition for the runtime API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from agent_runtime.api.membership import (
    HttpWorkspaceMembershipResolver,
    InMemoryWorkspaceMembershipResolver,
    MembershipResolverUnavailable,
    WorkspaceMembershipResolver,
)
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
from runtime_api.sse.event_bus import (
    EventBusBackend,
    InMemoryEventBus,
)
from runtime_api.sse.postgres_event_bus import PostgresEventBus
from runtime_worker import RuntimeWorker


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
            # P2 — start the cross-process LISTEN/NOTIFY bus task if the
            # Postgres backend is configured. Must run after the store is
            # open (the bus borrows the same DATABASE_URL) and before the
            # in-process worker so any startup events the worker emits are
            # immediately routable.
            await cls.start_event_bus(app)
            await cls.start_in_process_worker(app)
            try:
                yield
            finally:
                await cls.stop_in_process_worker(app)
                # Stop the bus AFTER the worker so any final events the
                # worker writes during shutdown are delivered to SSE
                # clients before the listener disconnects.
                await cls.stop_event_bus(app)
                await cls.close_async_store(app)

        app = FastAPI(title="Agent Runtime API", version="1", lifespan=lifespan)
        app.add_middleware(RequestContextMiddleware)
        if configure_telemetry_on_create:
            TelemetryBootstrap.instrument_fastapi(app)
        configured_service = service or cls.default_service(app)
        app.state.runtime_api_service = configured_service
        # P22 PR 1 — Coordinator shims. Constructed alongside the legacy
        # service so PR 2 can migrate routes one call site at a time. Each
        # shim forwards to the legacy class; implementation moves into the
        # shims in PR 4. See docs/refactor/19-runtime-api-service-split.md.
        from agent_runtime.api.approval_coordinator import ApprovalCoordinator
        from agent_runtime.api.conversation_coordinator import ConversationCoordinator
        from agent_runtime.api.conversation_query_service import (
            ConversationQueryService,
        )
        from agent_runtime.api.run_coordinator import RunCoordinator
        from agent_runtime.api.workspace_coordinator import WorkspaceCoordinator

        app.state.run_coordinator = RunCoordinator(legacy=configured_service)
        app.state.approval_coordinator = ApprovalCoordinator(legacy=configured_service)
        app.state.conversation_coordinator = ConversationCoordinator(
            legacy=configured_service
        )
        app.state.conversation_query_service = ConversationQueryService(
            legacy=configured_service
        )
        app.state.workspace_coordinator = WorkspaceCoordinator(
            legacy=configured_service
        )
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
        # PR A3 / 8.0.3c — owner-driven self-fork. Independent from the
        # share-fork service above (no share-snapshot dependency); the
        # only required state is persistence + audit.
        app.state.self_fork_service = cls.default_self_fork_service(app)

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
        event_bus = cls.default_event_bus(settings)
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
        # PR 1.4.1 — production wires the HTTP membership resolver when
        # the trusted backend lane is configured (BACKEND_BASE_URL +
        # ENTERPRISE_SERVICE_TOKEN). When either is missing we fall back
        # to the in-memory empty resolver — that path is now used only
        # by dev / single-process runs where forwarded approvals cannot
        # reach a different workspace anyway. Tests wire their own
        # ``InMemoryWorkspaceMembershipResolver`` via the constructor.
        membership_resolver = cls.default_membership_resolver()
        app.state.runtime_settings = settings
        app.state.runtime_event_bus = event_bus
        app.state.runtime_membership_resolver = membership_resolver
        # PR 4.4.7 Phase 2 (Slice B) — wire the suggestible-connectors
        # resolver. Without this the runtime defaults to ``Null`` and
        # ``runtime_context.suggested_connectors`` is always empty, so
        # the system prompt skips the catalog suggestions section and
        # the agent has no idea Linear / Notion / etc. exist when the
        # user asks "can you connect me to Linear". The factory falls
        # back to ``MCP_BACKEND_REGISTRY_URL`` when ``BACKEND_BASE_URL``
        # isn't set so dev (``make dev``) gets the production behavior
        # without a Makefile change.
        from agent_runtime.api.suggestible_connectors_resolver import (
            SuggestibleConnectorsResolverFactory,
        )

        suggestible_resolver = SuggestibleConnectorsResolverFactory.default()
        ports = RuntimeAdapterFactory.from_settings(settings)
        app.state.runtime_ports = ports
        return RuntimeApiService(
            persistence=ports.persistence,
            event_store=ports.event_store,
            queue=ports.queue,
            settings=settings,
            on_event_appended=event_bus.notify_sync,
            notification_dispatcher=notification_dispatcher
            if isinstance(notification_dispatcher, InboxAndEmailNotificationDispatcher)
            else LoggingNotificationDispatcher(),
            membership_resolver=membership_resolver,
            suggestible_connectors_resolver=suggestible_resolver,
        )

    @classmethod
    def default_membership_resolver(cls) -> WorkspaceMembershipResolver:
        """Pick the right resolver for the current deployment.

        Production wires :class:`HttpWorkspaceMembershipResolver` when the
        trusted backend lane is fully configured (``BACKEND_BASE_URL`` +
        ``ENTERPRISE_SERVICE_TOKEN``). If either env var is missing we
        return an empty :class:`InMemoryWorkspaceMembershipResolver` so
        the wire surface is intact (the runtime still calls
        ``is_active_member``) but every check returns ``False`` — the
        same conservative-deny behaviour the resolver shipped with
        before this wiring landed. Tests bypass this method by passing
        their own resolver to :class:`RuntimeApiService`.
        """

        import os

        backend_base_url = os.environ.get("BACKEND_BASE_URL", "").strip()
        service_token = os.environ.get("ENTERPRISE_SERVICE_TOKEN", "").strip()
        if not backend_base_url or not service_token:
            return InMemoryWorkspaceMembershipResolver()
        return HttpWorkspaceMembershipResolver(
            fetch=cls._httpx_membership_fetcher(),
            backend_base_url=backend_base_url,
            service_token=service_token,
        )

    @classmethod
    def _httpx_membership_fetcher(cls):
        """Return a small ``HttpFetcher`` callable backed by httpx.

        Kept inside the factory (not module-level) so the import of
        httpx is local to the wiring path and tests that don't exercise
        production composition don't pay for the import.
        """

        import httpx

        async def fetch(
            url: str, headers: dict[str, str]
        ) -> tuple[int, dict[str, object]]:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(url, headers=headers)
            except (httpx.HTTPError, OSError) as exc:
                raise MembershipResolverUnavailable(
                    "Identity backend unreachable while resolving membership."
                ) from exc
            try:
                body = response.json() if response.content else {}
            except ValueError:
                body = {}
            return response.status_code, body

        return fetch

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

        ports = getattr(app.state, "runtime_ports", None)
        if ports is None:  # pragma: no cover — only hit when boot has no ports
            return DraftService(store=InMemoryDraftStore())

        event_producer = RuntimeEventProducer(
            persistence=ports.persistence,
            event_store=ports.event_store,
        )
        return DraftService(
            store=ports.draft_store,
            persistence=ports.persistence,
            auth_gate=cls._draft_auth_gate(app),
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
        Production and dev always have async ports configured.
        """

        from agent_runtime.api.share_service import ShareService
        from runtime_adapters.in_memory.share_store import InMemoryShareStore

        ports = getattr(app.state, "runtime_ports", None)
        ports = ports
        if ports is None:  # pragma: no cover — only hit when boot has no ports
            return None
        share_store = getattr(ports, "share_store", None) or InMemoryShareStore()
        api_service = getattr(app.state, "runtime_api_service", None)
        if api_service is None:
            return None
        # Reuse the runtime API service's async ports directly — both
        # surfaces share the same async-native InMemoryRuntimeApiStore
        # (or PostgresRuntimeApiStore in production).
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

        ports = getattr(app.state, "runtime_ports", None)
        ports = ports
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
    def default_self_fork_service(cls, app: FastAPI):
        """Wire :class:`SelfForkService` (PR A3 / 8.0.3c).

        Returns ``None`` when the runtime API service isn't yet wired —
        the self-fork route then surfaces 503 to callers. The service
        only needs persistence + audit; no share-snapshot port.
        """

        from agent_runtime.api.self_fork import SelfForkService
        from runtime_worker.audit import WorkerAuditEmitter

        api_service = getattr(app.state, "runtime_api_service", None)
        if api_service is None:
            return None
        return SelfForkService(
            persistence=api_service.persistence,
            audit=WorkerAuditEmitter(api_service.persistence),
        )

    @classmethod
    def default_workspace_feed_service(cls, app: FastAPI):
        """Wire the Workspace pane data feeds (PR 1.5).

        The factory pre-builds the satellite stores for whichever backend is
        configured, so this just hands them off — no backend branching here.
        """

        from agent_runtime.api.workspace_feed_service import WorkspaceFeedService
        from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
        from runtime_adapters.in_memory.source_store import InMemorySourceStore
        from runtime_adapters.in_memory.subagent_store import InMemorySubagentStore

        ports = getattr(app.state, "runtime_ports", None)
        if ports is None:  # pragma: no cover — only hit when boot has no ports
            return WorkspaceFeedService(
                subagent_store=InMemorySubagentStore(None),
                source_store=InMemorySourceStore(InMemoryCitationStore()),
            )
        return WorkspaceFeedService(
            subagent_store=ports.subagent_store,
            source_store=ports.source_store,
        )

    @classmethod
    def default_event_bus(cls, settings: RuntimeSettings) -> EventBusBackend:
        """Pick the SSE event bus based on configuration.

        ``in_memory`` (the default) returns the legacy single-process
        ``InMemoryEventBus`` singleton — unchanged from pre-P2 behavior so
        dev / test paths see no change.

        ``postgres`` constructs a :class:`PostgresEventBus` whose
        connection factory opens a dedicated psycopg ``AsyncConnection``
        (autocommit-enabled, since ``LISTEN`` must take effect outside a
        transaction). The bus is started + stopped by
        :meth:`start_event_bus` / :meth:`stop_event_bus` in the lifespan.
        """

        backend = settings.execution.event_bus_backend.lower()
        if backend == "postgres":
            database_url = settings.store.database_url
            if not database_url:
                raise ValueError(
                    "RUNTIME_EVENT_BUS_BACKEND=postgres requires DATABASE_URL "
                    "to be configured."
                )

            async def _connection_factory() -> object:
                import psycopg

                # ``LISTEN`` is connection-bound and must run outside a
                # transaction; autocommit is required so the LISTEN takes
                # effect immediately and notifications are delivered as
                # they arrive.
                return await psycopg.AsyncConnection.connect(
                    database_url, autocommit=True
                )

            return PostgresEventBus(connection_factory=_connection_factory)
        return InMemoryEventBus.get_default()

    @classmethod
    async def start_event_bus(cls, app: FastAPI) -> None:
        """Start the SSE event-bus background task if it has one.

        ``InMemoryEventBus`` is purely in-process and has no background
        task, so this is a no-op for it. ``PostgresEventBus`` spawns its
        ``listen_loop`` task here and tears it down in
        :meth:`stop_event_bus`.
        """

        bus = getattr(app.state, "runtime_event_bus", None)
        start = getattr(bus, "start", None)
        if start is None:
            return
        await start()

    @classmethod
    async def stop_event_bus(cls, app: FastAPI) -> None:
        """Stop the SSE event-bus background task if it has one."""

        bus = getattr(app.state, "runtime_event_bus", None)
        stop = getattr(bus, "stop", None)
        if stop is None:
            return
        await stop()

    @classmethod
    async def open_async_store(cls, app: FastAPI) -> None:
        """Open + migrate the async store on startup if one was configured."""

        ports = getattr(app.state, "runtime_ports", None)
        if ports is None:
            return
        await ports.lifecycle.open()
        await ports.lifecycle.migrate()

    @classmethod
    async def close_async_store(cls, app: FastAPI) -> None:
        """Close the async store on shutdown."""

        ports = getattr(app.state, "runtime_ports", None)
        if ports is None:
            return
        await ports.lifecycle.close()

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
            app.state, "runtime_ports", None
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
            conversation_tool_ordinal_store=getattr(
                ports, "conversation_tool_ordinal_store", None
            ),
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
