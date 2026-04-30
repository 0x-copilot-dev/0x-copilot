"""Runtime adapter composition from env-backed settings."""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.api.ports import EventStorePort, PersistencePort, RuntimeQueuePort
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.postgres import PostgresRuntimeApiStore


@dataclass(frozen=True)
class RuntimePorts:
    """Composed runtime persistence, event, and queue ports."""

    persistence: PersistencePort
    event_store: EventStorePort
    queue: RuntimeQueuePort
    backend: str


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
