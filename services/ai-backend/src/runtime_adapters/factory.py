"""Runtime adapter composition from env-backed settings."""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.api.async_ports import (
    AsyncEventStorePort,
    AsyncPersistencePort,
    AsyncRuntimeQueuePort,
)
from agent_runtime.api.ports import EventStorePort, PersistencePort, RuntimeQueuePort
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import (
    AsyncInMemoryRuntimeApiStore,
    InMemoryRuntimeApiStore,
)
from runtime_adapters.postgres import (
    AsyncPostgresRuntimeApiStore,
    PostgresRuntimeApiStore,
)


@dataclass(frozen=True)
class RuntimePorts:
    """Composed runtime persistence, event, and queue ports (sync)."""

    persistence: PersistencePort
    event_store: EventStorePort
    queue: RuntimeQueuePort
    backend: str


@dataclass(frozen=True)
class AsyncRuntimePorts:
    """Composed runtime persistence, event, and queue ports (async).

    The store underneath owns its connection pool. Callers MUST ``await
    store.open()`` before use and ``await store.close()`` on shutdown — the
    factory does not do this so the lifecycle stays tied to the FastAPI
    lifespan / worker entrypoint.
    """

    persistence: AsyncPersistencePort
    event_store: AsyncEventStorePort
    queue: AsyncRuntimeQueuePort
    backend: str
    # Concrete store reference so the lifespan owner can call open()/close()
    # without re-introspecting the trio of ports.
    store: AsyncPostgresRuntimeApiStore | AsyncInMemoryRuntimeApiStore


class RuntimeAdapterFactory:
    """Build runtime adapters for API and worker processes."""

    @classmethod
    def from_settings(
        cls, settings: RuntimeSettings, *, migrate: bool = True
    ) -> RuntimePorts:
        backend = settings.store.backend
        if backend == "in_memory":
            store = InMemoryRuntimeApiStore()
            return RuntimePorts(
                persistence=store,
                event_store=store,
                queue=store,
                backend=backend,
            )
        if backend == "postgres":
            if settings.store.database_url is None:
                raise AgentRuntimeError(
                    RuntimeErrorCode.CONFIGURATION_ERROR,
                    "DATABASE_URL is required when RUNTIME_STORE_BACKEND=postgres.",
                    retryable=False,
                )
            store = PostgresRuntimeApiStore(settings.store.database_url)
            if migrate:
                store.migrate()
            return RuntimePorts(
                persistence=store,
                event_store=store,
                queue=store,
                backend=backend,
            )
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            f"Unsupported runtime store backend '{backend}'.",
            retryable=False,
        )

    @classmethod
    def async_from_settings(cls, settings: RuntimeSettings) -> AsyncRuntimePorts:
        """Build async runtime ports.

        The store's pool is *not* opened here. Caller (FastAPI lifespan or
        worker entrypoint) must:

            ports = RuntimeAdapterFactory.async_from_settings(settings)
            await ports.store.open()
            if migrate_needed: await ports.store.migrate()
            try:
                ...
            finally:
                await ports.store.close()
        """

        backend = settings.store.backend
        if backend == "in_memory_async":
            store: AsyncPostgresRuntimeApiStore | AsyncInMemoryRuntimeApiStore = (
                AsyncInMemoryRuntimeApiStore()
            )
            return AsyncRuntimePorts(
                persistence=store,
                event_store=store,
                queue=store,
                backend=backend,
                store=store,
            )
        if backend == "postgres_async":
            if settings.store.database_url is None:
                raise AgentRuntimeError(
                    RuntimeErrorCode.CONFIGURATION_ERROR,
                    "DATABASE_URL is required when RUNTIME_STORE_BACKEND=postgres_async.",
                    retryable=False,
                )
            store = AsyncPostgresRuntimeApiStore(settings.store.database_url)
            return AsyncRuntimePorts(
                persistence=store,
                event_store=store,
                queue=store,
                backend=backend,
                store=store,
            )
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            f"Unsupported async runtime store backend '{backend}'. "
            "Use 'in_memory_async' or 'postgres_async'.",
            retryable=False,
        )
