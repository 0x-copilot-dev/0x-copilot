"""FastAPI app composition for the runtime API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from agent_runtime.api.service import RuntimeApiService
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_api.http.errors import RuntimeApiError, RuntimeApiErrorMapper
from runtime_api.http.routes import RuntimeApiRouter
from runtime_worker import RuntimeWorker


class RuntimeApiAppFactory:
    """Create a FastAPI app with dependency-inverted runtime API ports."""

    @classmethod
    def create_app(cls, service: RuntimeApiService | None = None) -> FastAPI:
        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            await cls.start_in_process_worker(app)
            try:
                yield
            finally:
                await cls.stop_in_process_worker(app)

        app = FastAPI(title="Agent Runtime API", version="1", lifespan=lifespan)
        configured_service = service or cls.default_service(app)
        app.state.runtime_api_service = configured_service
        app.include_router(RuntimeApiRouter.create_router())
        app.add_exception_handler(RuntimeApiError, RuntimeApiErrorMapper.handle_runtime_api_error)
        app.add_exception_handler(ValidationError, RuntimeApiErrorMapper.handle_validation_error)
        app.add_exception_handler(
            RequestValidationError,
            RuntimeApiErrorMapper.handle_request_validation_error,
        )
        app.add_exception_handler(Exception, RuntimeApiErrorMapper.handle_unexpected_error)
        return app

    @classmethod
    def default_service(cls, app: FastAPI) -> RuntimeApiService:
        settings = RuntimeSettings.load()
        ports = RuntimeAdapterFactory.from_settings(settings)
        app.state.runtime_ports = ports
        app.state.runtime_settings = settings
        return RuntimeApiService(
            persistence=ports.persistence,
            event_store=ports.event_store,
            queue=ports.queue,
            settings=settings,
        )

    @classmethod
    async def start_in_process_worker(cls, app: FastAPI) -> None:
        """Run a same-process worker for local in-memory debugging."""

        settings = getattr(app.state, "runtime_settings", None)
        ports = getattr(app.state, "runtime_ports", None)
        if settings is None or ports is None:
            return
        if settings.store.backend != "in_memory":
            return
        if not settings.execution.start_in_process_worker:
            return
        worker = RuntimeWorker(
            persistence=ports.persistence,
            event_store=ports.event_store,
            queue=ports.queue,
            settings=settings,
            lock_seconds=settings.execution.worker_lock_seconds,
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
