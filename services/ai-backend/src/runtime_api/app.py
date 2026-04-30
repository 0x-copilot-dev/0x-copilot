"""FastAPI app composition for the runtime API."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from agent_runtime.api.service import RuntimeApiService
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.http.errors import RuntimeApiError, RuntimeApiErrorMapper
from runtime_api.http.routes import RuntimeApiRouter


class RuntimeApiAppFactory:
    """Create a FastAPI app with dependency-inverted runtime API ports."""

    @classmethod
    def create_app(cls, service: RuntimeApiService | None = None) -> FastAPI:
        app = FastAPI(title="Agent Runtime API", version="1")
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
        store = InMemoryRuntimeApiStore()
        app.state.runtime_api_store = store
        return RuntimeApiService(persistence=store, event_store=store, queue=store)


app = RuntimeApiAppFactory.create_app()
