"""Runtime adapter composition from env-backed settings."""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.api.ports import (
    EventStorePort,
    PersistencePort,
    RuntimeQueuePort,
)
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.persistence.ports import (
    ConversationToolOrdinalStorePort,
    DraftStorePort,
    ShareStorePort,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.conversation_tool_ordinal_store import (
    InMemoryConversationToolOrdinalStore,
)
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_adapters.in_memory.share_store import InMemoryShareStore
from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_adapters.postgres.conversation_tool_ordinal_store import (
    PostgresConversationToolOrdinalStore,
)
from runtime_adapters.postgres.draft_store import PostgresDraftStore
from runtime_adapters.postgres.share_store import PostgresShareStore


@dataclass(frozen=True)
class RuntimePorts:
    """Composed runtime ports (async-native)."""

    persistence: PersistencePort
    event_store: EventStorePort
    queue: RuntimeQueuePort
    backend: str
    # Concrete store reference so the lifespan owner can call open()/close()
    # without re-introspecting the trio of ports.
    store: PostgresRuntimeApiStore | InMemoryRuntimeApiStore
    # PR 1.3.5 — Workspace-pane Draft store. Postgres backend wraps the
    # parent store's pool + FieldCodec; in_memory backends use the
    # process-local InMemoryDraftStore.
    draft_store: DraftStorePort | None = None
    # PR 6.1 — conversation share store. Backs both the recipient view
    # (ShareService) and PR 6.2's fork service.
    share_store: ShareStorePort | None = None
    # PR 04 — persistent (conversation_ordinal ↔ tool_call_id) binding store.
    conversation_tool_ordinal_store: ConversationToolOrdinalStorePort | None = None


class RuntimeAdapterFactory:
    """Build runtime adapters for API and worker processes."""

    @classmethod
    def from_settings(
        cls, settings: RuntimeSettings, *, role: str = "api"
    ) -> RuntimePorts:
        """Build async runtime ports.

        ``role`` distinguishes the API process from the worker process so the
        pool's ``application_name`` shows up greppable in ``pg_stat_activity``.

        The store's pool is *not* opened here. The caller (FastAPI lifespan
        or worker entrypoint) must:

            ports = RuntimeAdapterFactory.from_settings(settings)
            await ports.store.open()
            await ports.store.migrate()
            try:
                ...
            finally:
                await ports.store.close()
        """

        backend = settings.store.backend
        # P4 — single source of truth for the cursor-write consolidation
        # flag. Both adapters honor it identically; producers auto-detect
        # via ``event_store.consolidates_cursor_writes``.
        consolidated_writes = settings.execution.consolidated_event_writes
        # P2 — when the SSE bus is the Postgres LISTEN/NOTIFY backend, the
        # postgres adapter must fire a NOTIFY after every append so the
        # API process's listener wakes the SSE adapter cross-process.
        # In-memory bus doesn't need this — that path uses asyncio.Condition.
        notify_after_append = settings.execution.event_bus_backend.lower() == "postgres"
        # ``in_memory`` is the legacy alias for ``in_memory_async`` — both
        # route to the async-native InMemoryRuntimeApiStore.
        if backend in {"in_memory_async", "in_memory"}:
            store: PostgresRuntimeApiStore | InMemoryRuntimeApiStore = (
                InMemoryRuntimeApiStore(consolidated_writes=consolidated_writes)
            )
            return RuntimePorts(
                persistence=store,
                event_store=store,
                queue=store,
                backend=backend,
                store=store,
                draft_store=InMemoryDraftStore(),
                share_store=InMemoryShareStore(),
                conversation_tool_ordinal_store=InMemoryConversationToolOrdinalStore(),
            )
        if backend == "postgres":
            if settings.store.database_url is None:
                raise AgentRuntimeError(
                    RuntimeErrorCode.CONFIGURATION_ERROR,
                    "DATABASE_URL is required when RUNTIME_STORE_BACKEND=postgres.",
                    retryable=False,
                )
            store = PostgresRuntimeApiStore(
                settings.store.database_url,
                role=role,
                consolidated_writes=consolidated_writes,
                notify_after_append=notify_after_append,
            )
            return RuntimePorts(
                persistence=store,
                event_store=store,
                queue=store,
                backend=backend,
                store=store,
                draft_store=PostgresDraftStore(store),
                share_store=PostgresShareStore(store),
                conversation_tool_ordinal_store=PostgresConversationToolOrdinalStore(
                    store
                ),
            )
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            f"Unsupported async runtime store backend '{backend}'. "
            "Use 'in_memory_async' or 'postgres'.",
            retryable=False,
        )
