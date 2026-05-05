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
from agent_runtime.persistence.ports import DraftStorePort
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import (
    AsyncInMemoryRuntimeApiStore,
    InMemoryRuntimeApiStore,
)
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_adapters.postgres.draft_store import PostgresDraftStore


@dataclass(frozen=True)
class RuntimePorts:
    """Composed runtime ports (sync test fakes — kept for in_memory backend)."""

    persistence: PersistencePort
    event_store: EventStorePort
    queue: RuntimeQueuePort
    backend: str
    # PR 1.3.5 — Workspace-pane Draft store, shared by the API (DraftService)
    # and the worker (DraftBackend constructed per run).
    draft_store: DraftStorePort | None = None


@dataclass(frozen=True)
class AsyncRuntimePorts:
    """Composed runtime ports (async)."""

    persistence: AsyncPersistencePort
    event_store: AsyncEventStorePort
    queue: AsyncRuntimeQueuePort
    backend: str
    # Concrete store reference so the lifespan owner can call open()/close()
    # without re-introspecting the trio of ports.
    store: PostgresRuntimeApiStore | AsyncInMemoryRuntimeApiStore
    # PR 1.3.5 — Workspace-pane Draft store. Postgres backend wraps the
    # parent store's pool + FieldCodec; in_memory backends use the
    # process-local InMemoryDraftStore.
    draft_store: DraftStorePort | None = None


class RuntimeAdapterFactory:
    """Build runtime adapters for API and worker processes."""

    @classmethod
    def from_settings(
        cls, settings: RuntimeSettings, *, migrate: bool = True
    ) -> RuntimePorts:
        """Build the sync port trio for the legacy ``in_memory`` backend.

        ``postgres`` is no longer offered as a sync backend — it is async
        only. Callers asking for postgres should use
        :meth:`async_from_settings`.
        """

        backend = settings.store.backend
        if backend == "in_memory":
            store = InMemoryRuntimeApiStore()
            return RuntimePorts(
                persistence=store,
                event_store=store,
                queue=store,
                backend=backend,
                draft_store=InMemoryDraftStore(),
            )
        if backend == "postgres":
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "RUNTIME_STORE_BACKEND=postgres requires the async wiring; "
                "use RuntimeAdapterFactory.async_from_settings.",
                retryable=False,
            )
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            f"Unsupported runtime store backend '{backend}'.",
            retryable=False,
        )

    @classmethod
    def async_from_settings(
        cls, settings: RuntimeSettings, *, role: str = "api"
    ) -> AsyncRuntimePorts:
        """Build async runtime ports.

        ``role`` distinguishes the API process from the worker process so the
        pool's ``application_name`` shows up greppable in ``pg_stat_activity``.

        The store's pool is *not* opened here. The caller (FastAPI lifespan
        or worker entrypoint) must:

            ports = RuntimeAdapterFactory.async_from_settings(settings)
            await ports.store.open()
            await ports.store.migrate()
            try:
                ...
            finally:
                await ports.store.close()
        """

        backend = settings.store.backend
        if backend == "in_memory_async":
            store: PostgresRuntimeApiStore | AsyncInMemoryRuntimeApiStore = (
                AsyncInMemoryRuntimeApiStore()
            )
            return AsyncRuntimePorts(
                persistence=store,
                event_store=store,
                queue=store,
                backend=backend,
                store=store,
                draft_store=InMemoryDraftStore(),
            )
        if backend == "postgres":
            if settings.store.database_url is None:
                raise AgentRuntimeError(
                    RuntimeErrorCode.CONFIGURATION_ERROR,
                    "DATABASE_URL is required when RUNTIME_STORE_BACKEND=postgres.",
                    retryable=False,
                )
            store = PostgresRuntimeApiStore(settings.store.database_url, role=role)
            return AsyncRuntimePorts(
                persistence=store,
                event_store=store,
                queue=store,
                backend=backend,
                store=store,
                draft_store=PostgresDraftStore(store),
            )
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            f"Unsupported async runtime store backend '{backend}'. "
            "Use 'in_memory_async' or 'postgres'.",
            retryable=False,
        )
