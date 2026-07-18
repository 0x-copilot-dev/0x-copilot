"""Runtime adapter composition from env-backed settings."""

from __future__ import annotations

import os
from dataclasses import dataclass

from copilot_service_contracts.deployment_profile import (
    ENV_DEPLOYMENT_PROFILE,
    PROFILE_SINGLE_USER_DESKTOP,
)

from agent_runtime.api.ports import (
    EventStorePort,
    PersistencePort,
    RuntimeQueuePort,
    RuntimeStoreLifecyclePort,
)
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.persistence.ports import (
    ConversationToolOrdinalStorePort,
    DraftStorePort,
    ShareStorePort,
    SourceStorePort,
    SubagentStorePort,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
from runtime_adapters.in_memory.conversation_tool_ordinal_store import (
    InMemoryConversationToolOrdinalStore,
)
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_adapters.in_memory.share_store import InMemoryShareStore
from runtime_adapters.in_memory.source_store import InMemorySourceStore
from runtime_adapters.in_memory.subagent_store import InMemorySubagentStore
from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_adapters.postgres.conversation_tool_ordinal_store import (
    PostgresConversationToolOrdinalStore,
)
from runtime_adapters.postgres.draft_store import PostgresDraftStore
from runtime_adapters.postgres.share_store import PostgresShareStore
from runtime_adapters.postgres.source_store import PostgresSourceStore
from runtime_adapters.postgres.subagent_store import PostgresSubagentStore


def _build_file_ports(settings: RuntimeSettings) -> "RuntimePorts":
    """Construct the file-native ``RuntimePorts`` for the desktop profile.

    Fails closed (``CONFIGURATION_ERROR``) unless every precondition holds:
    ``RUNTIME_STORE_BACKEND=file`` **and**
    ``ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop`` **and**
    ``RUNTIME_FILE_STORE_ROOT`` is set. The web/Postgres/in-memory paths are
    never reached from here, so their behavior is untouched.
    """

    profile = os.environ.get(ENV_DEPLOYMENT_PROFILE, "").strip().lower()
    if profile != PROFILE_SINGLE_USER_DESKTOP:
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "RUNTIME_STORE_BACKEND=file requires "
            f"{ENV_DEPLOYMENT_PROFILE}={PROFILE_SINGLE_USER_DESKTOP} "
            f"(got {profile or 'unset'!r}).",
            retryable=False,
        )
    root = settings.store.file_store_root
    if not root:
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "RUNTIME_FILE_STORE_ROOT is required when RUNTIME_STORE_BACKEND=file.",
            retryable=False,
        )

    # Imported lazily so the file backend (and its sqlite dependency) is only
    # pulled in for the desktop path — the web image never imports it.
    from runtime_adapters.file import (
        FileConversationToolOrdinalStore,
        FileDraftStore,
        FileRuntimeApiStore,
        FileShareStore,
    )
    from runtime_adapters.file.citation_store import FileCitationStore
    from runtime_adapters.in_memory.source_store import InMemorySourceStore
    from runtime_adapters.in_memory.subagent_store import InMemorySubagentStore

    file_store = FileRuntimeApiStore(
        root,
        max_bytes=settings.store.file_store_max_bytes,
        retention_days=settings.store.file_store_retention_days,
    )
    layout = file_store.layout
    citation_store = FileCitationStore(layout)
    return RuntimePorts(
        persistence=file_store,
        event_store=file_store,
        queue=file_store,
        backend="file",
        lifecycle=file_store,
        draft_store=FileDraftStore(layout),
        share_store=FileShareStore(layout),
        conversation_tool_ordinal_store=FileConversationToolOrdinalStore(layout),
        # Pure projectors over the file store's file-backed materialized view.
        subagent_store=InMemorySubagentStore(file_store),
        source_store=InMemorySourceStore(citation_store),
    )


@dataclass(frozen=True)
class RuntimePorts:
    """Composed runtime ports (async-native).

    Every consumer-facing dependency is typed against a Protocol — no
    concrete class names leak. The lifespan owner drives the store via
    :attr:`lifecycle`; the satellite stores are pre-built so consumers
    never need to know which backend is wired in.
    """

    persistence: PersistencePort
    event_store: EventStorePort
    queue: RuntimeQueuePort
    backend: str
    lifecycle: RuntimeStoreLifecyclePort
    draft_store: DraftStorePort
    share_store: ShareStorePort
    conversation_tool_ordinal_store: ConversationToolOrdinalStorePort
    subagent_store: SubagentStorePort
    source_store: SourceStorePort
    # Postgres-only escape hatch. Populated only when ``backend == "postgres"``
    # so the opt-in ``DbStatementMetricsCollector`` can reach the pool via
    # ``_role_connection``. Every other consumer should use the typed ports
    # above and stay backend-agnostic.
    postgres_store: PostgresRuntimeApiStore | None = None


class RuntimeAdapterFactory:
    """Build runtime adapters for API and worker processes."""

    @classmethod
    def from_store(cls, store: InMemoryRuntimeApiStore) -> RuntimePorts:
        """Build a minimal in-memory :class:`RuntimePorts` from an existing store.

        Tests use this helper to construct ports without coupling to
        coordinator internals.  Every satellite store is freshly
        constructed so they share no state with other test instances.
        """
        return RuntimePorts(
            persistence=store,
            event_store=store,
            queue=store,
            backend="in_memory",
            lifecycle=store,
            draft_store=InMemoryDraftStore(),
            share_store=InMemoryShareStore(),
            conversation_tool_ordinal_store=InMemoryConversationToolOrdinalStore(),
            subagent_store=InMemorySubagentStore(store),
            source_store=InMemorySourceStore(InMemoryCitationStore()),
        )

    @classmethod
    def from_settings(
        cls, settings: RuntimeSettings, *, role: str = "api"
    ) -> RuntimePorts:
        """Construct and return all runtime ports from application settings.

        ``role`` is stamped on the pool's ``application_name`` so connections
        are identifiable in ``pg_stat_activity``. The caller must open and
        close the pool via ``ports.lifecycle``.
        """

        backend = settings.store.backend
        # When the SSE bus uses Postgres LISTEN/NOTIFY, the adapter must fire a
        # NOTIFY after every event append so the API process's listener wakes
        # the SSE handler cross-process.  The in-memory bus uses asyncio.Condition
        # and does not need an explicit notification.
        notify_after_append = settings.resolved_event_bus_backend() == "postgres"
        # ``in_memory`` is the legacy alias for ``in_memory_async`` — both
        # route to the async-native InMemoryRuntimeApiStore.
        if backend in {"in_memory_async", "in_memory"}:
            in_memory_store = InMemoryRuntimeApiStore()
            return RuntimePorts(
                persistence=in_memory_store,
                event_store=in_memory_store,
                queue=in_memory_store,
                backend=backend,
                lifecycle=in_memory_store,
                draft_store=InMemoryDraftStore(),
                share_store=InMemoryShareStore(),
                conversation_tool_ordinal_store=InMemoryConversationToolOrdinalStore(),
                subagent_store=InMemorySubagentStore(in_memory_store),
                source_store=InMemorySourceStore(InMemoryCitationStore()),
            )
        if backend == "postgres":
            if settings.store.database_url is None:
                raise AgentRuntimeError(
                    RuntimeErrorCode.CONFIGURATION_ERROR,
                    "DATABASE_URL is required when RUNTIME_STORE_BACKEND=postgres.",
                    retryable=False,
                )
            postgres_store = PostgresRuntimeApiStore(
                settings.store.database_url,
                role=role,
                notify_after_append=notify_after_append,
            )
            return RuntimePorts(
                persistence=postgres_store,
                event_store=postgres_store,
                queue=postgres_store,
                backend=backend,
                lifecycle=postgres_store,
                draft_store=PostgresDraftStore(postgres_store),
                share_store=PostgresShareStore(postgres_store),
                conversation_tool_ordinal_store=PostgresConversationToolOrdinalStore(
                    postgres_store
                ),
                subagent_store=PostgresSubagentStore(postgres_store),
                source_store=PostgresSourceStore(postgres_store),
                postgres_store=postgres_store,
            )
        if backend == "file":
            return _build_file_ports(settings)
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            f"Unsupported async runtime store backend '{backend}'. "
            "Use 'in_memory_async', 'postgres', or 'file'.",
            retryable=False,
        )
