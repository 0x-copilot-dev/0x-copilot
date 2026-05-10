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
    """Composed runtime ports (sync test fakes — kept for in_memory backend)."""

    persistence: PersistencePort
    event_store: EventStorePort
    queue: RuntimeQueuePort
    backend: str
    # PR 1.3.5 — Workspace-pane Draft store, shared by the API (DraftService)
    # and the worker (DraftBackend constructed per run).
    draft_store: DraftStorePort | None = None
    # PR 6.1 — conversation share store. Same role as ``draft_store``;
    # backs both the recipient view (ShareService) and PR 6.2's fork
    # service (via ``ShareSnapshotPort.resolve_by_token`` on ShareService).
    share_store: ShareStorePort | None = None
    # PR 04 — persistent (conversation_ordinal ↔ tool_call_id) binding
    # store. Backs the model-declared citation system; reads at run /
    # approval-resume bind, writes on every allocate. Optional so call
    # sites that build ports manually keep compiling.
    conversation_tool_ordinal_store: ConversationToolOrdinalStorePort | None = None


@dataclass(frozen=True)
class AsyncRuntimePorts:
    """Composed runtime ports (async)."""

    persistence: AsyncPersistencePort
    event_store: AsyncEventStorePort
    queue: AsyncRuntimeQueuePort
    backend: str
    # Concrete store reference so the lifespan owner can call open()/close()
    # without re-introspecting the trio of ports.
    store: PostgresRuntimeApiStore | InMemoryRuntimeApiStore
    # PR 1.3.5 — Workspace-pane Draft store. Postgres backend wraps the
    # parent store's pool + FieldCodec; in_memory backends use the
    # process-local InMemoryDraftStore.
    draft_store: DraftStorePort | None = None
    # PR 6.1 — conversation share store (see RuntimePorts.share_store).
    share_store: ShareStorePort | None = None
    # PR 04 — see RuntimePorts.conversation_tool_ordinal_store.
    conversation_tool_ordinal_store: ConversationToolOrdinalStorePort | None = None


class RuntimeAdapterFactory:
    """Build runtime adapters for API and worker processes."""

    @classmethod
    def from_settings(
        cls, settings: RuntimeSettings, *, migrate: bool = True
    ) -> RuntimePorts:
        """Sync factory — RETIRED.

        The runtime persistence layer is now async-native end-to-end (PR 2
        of the async-only ports refactor). Use :meth:`async_from_settings`.
        This method is kept only to surface a typed error for any caller
        that hasn't migrated yet; PR 5 deletes it entirely along with the
        sync :class:`RuntimePorts` dataclass.
        """

        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "RuntimeAdapterFactory.from_settings is retired; use "
            "async_from_settings — the runtime is async-native.",
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
        # ``in_memory`` is the legacy alias for ``in_memory_async`` — both
        # now route to the async-native InMemoryRuntimeApiStore. The sync
        # backend was retired with the sync port surface (PR 2 of the
        # async-only ports refactor).
        if backend in {"in_memory_async", "in_memory"}:
            store: PostgresRuntimeApiStore | InMemoryRuntimeApiStore = (
                InMemoryRuntimeApiStore()
            )
            return AsyncRuntimePorts(
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
            store = PostgresRuntimeApiStore(settings.store.database_url, role=role)
            return AsyncRuntimePorts(
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
