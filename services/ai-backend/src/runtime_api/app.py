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
from runtime_api.http.retention_routes import RetentionAdminRouter
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
            )
        ports = RuntimeAdapterFactory.from_settings(settings)
        app.state.runtime_ports = ports
        return RuntimeApiService(
            persistence=ports.persistence,
            event_store=ports.event_store,
            queue=ports.queue,
            settings=settings,
            on_event_appended=event_bus.notify_sync,
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
